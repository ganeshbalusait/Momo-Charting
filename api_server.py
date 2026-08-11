from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import gzip
from io import BytesIO
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
from PIL import Image
from alpaca.trading.enums import QueryOrderStatus
from dotenv import set_key

from agents.option_llm_supervisor import OptionLLMSupervisor
from alpaca_stream import AlpacaBarStream
from auth_service import AuthenticationError, AuthorizationError, AuthService
from schwab_stream import SchwabMarketStream, event_stream_cursor
from backtester import Backtester
from catalyst_engine import CatalystEngine
from config import ARTIFACTS_DIR, DATABASE_PATH, EASTERN_TZ, WATCHLIST_PATH, settings
from data.alpaca_client import AlpacaClient
from data.market_data import create_market_data_client
from data.schwab_client import SchwabClient
from data.schwab_otm_activity import (
    build_dashboard_response,
    classify_strikes,
    compute_signal_metrics,
    find_strongest_bearish_signal,
    find_strongest_bullish_signal,
    score_signal,
)
from data.tradier_client import TradierClient
from database.repository import TradingRepository
from execution.alpaca_paper_trader import AlpacaPaperTrader
from indicators import ema
from learning_engine import TradingLearningAgent
from scanner import MomentumScanner, _tos_mtf_ema_signal_payload, _tos_watchlist_mtf_signal_payload, scan_live_4h_volume, scan_live_price_change
from ganesh_higher_timeframe_signals import (
    SCHEMA_VERSION as GANESH_SCHEMA_VERSION,
    SIGNAL_MODE as GANESH_SIGNAL_MODE,
    SOURCE_AGGREGATION_MINUTES as GANESH_SOURCE_AGGREGATION_MINUTES,
    build_ganesh_higher_timeframe_signal_payload,
)
from schwab_oauth_callback import callback_listener_status, start_callback_listener


HOST = "127.0.0.1"
PORT = 3001
ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_OPTION_DELTA_CAP = 0.20
DEFAULT_OPTION_PREFERRED_DELTA = 0.10
DEFAULT_OPTION_MIN_EXPECTED_MOVE = 2.0
# Normalized option-chain contracts, memoized per raw chain payload.
# _option_chain_contracts is pure for a given (payload, side) but one payload
# build calls it ~20 times (and once per expiry inside the expected-move
# helpers), so the same thousands of contracts were re-parsed from strings
# over and over while holding the GIL. Keyed by id() with the payload kept
# alive in the entry so an id can never be recycled under a live key; entries
# are large, so only the few chains actually in flight are retained and
# insertion order gives simple FIFO eviction.
_OPTION_CHAIN_CONTRACT_CACHE: "OrderedDict[int, tuple[dict, dict[str, list[dict]]]]" = OrderedDict()
_OPTION_CHAIN_CONTRACT_CACHE_MAX_PAYLOADS = 6
_OPTION_CHAIN_CONTRACT_CACHE_LOCK = threading.Lock()


def _remember_option_chain_contracts(
    chain_payload: dict,
    map_key: str,
    contracts: list[dict],
) -> None:
    """Memoize one side's normalized contracts for this exact payload object."""
    payload_id = id(chain_payload)
    with _OPTION_CHAIN_CONTRACT_CACHE_LOCK:
        entry = _OPTION_CHAIN_CONTRACT_CACHE.get(payload_id)
        if entry is None or entry[0] is not chain_payload:
            entry = (chain_payload, {})
            _OPTION_CHAIN_CONTRACT_CACHE[payload_id] = entry
        entry[1][map_key] = contracts
        _OPTION_CHAIN_CONTRACT_CACHE.move_to_end(payload_id)
        while len(_OPTION_CHAIN_CONTRACT_CACHE) > _OPTION_CHAIN_CONTRACT_CACHE_MAX_PAYLOADS:
            _OPTION_CHAIN_CONTRACT_CACHE.popitem(last=False)


# Schwab's daily price-history endpoint accepts just under twenty calendar
# years for an explicit date range, and get_daily_bars doubles this value to
# cover non-trading days. 3,650 expands to 7,300 calendar days: deep enough
# for the 200-month level while staying inside the broker's accepted range
# (3,800 expanded past it and was rejected for newer symbols).
OI_FINDER_CHART_DAILY_SEED_LOOKBACK_DAYS = 3650
# Schwab's minute-frequency endpoint currently caps 30-minute candles at
# roughly nine months even when a twenty-year range is requested.  Ask for
# the full range so the broker returns its deepest genuine intraday tape, then
# prepend unmodified daily OHLC candles as an explicitly labelled archive.
# This keeps the 4H pane pannable across twenty years without inventing
# intraday prices that the broker did not provide.
OI_FINDER_CHART_INTRADAY_LOOKBACK_DAYS = 7300
OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS = 5 * 24 * 60 * 60
# A persisted tape whose newest bar is older than this is paintable but NOT
# authoritative: it must be promoted with a full rebuild rather than having
# today's minutes spliced onto days-old history. 26h spans a weekend-free
# overnight gap while still catching Friday tapes opened on Monday.
OI_FINDER_CHART_STALE_TAPE_SECONDS = 26 * 60 * 60
# Match the browser's REST reconciliation cadence. A ready chart never needs
# another broker request merely because the dashboard's five-second status
# poll ran; that caused constant history work and UI stalls.
OI_FINDER_CHART_REFRESH_SECONDS = 30.0
OI_FINDER_CHART_WARM_LIMIT = 24
OI_FINDER_CHAIN_DISK_CACHE_MAX_AGE_SECONDS = 5 * 24 * 60 * 60
OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION = 1
OI_FINDER_CHART_FULL_REFRESH_DEFER_SECONDS = 0.25
# The browser polls the dashboard every five seconds, but the full payload
# includes broker reconciliation and hundreds of historical-trade reads. All
# user actions explicitly invalidate this cache and the response overlays its
# live control state, so rebuilding the heavyweight portion once per minute is
# both responsive and dramatically cheaper than rebuilding it on every poll.
DASHBOARD_FULL_CACHE_TTL_SECONDS = 60.0

DEFAULT_OPTION_MIN_PRICE = 3.0
DEFAULT_OPTION_SIGNAL_LOOKBACK_BARS = 2
OI_SCANNER_MAX_DAYS_TO_EXPIRATION = 14
OI_FINDER_MAX_DAYS_TO_EXPIRATION = 31
EARNINGS_CALENDAR_DEFAULT_DAYS = 45
EARNINGS_CALENDAR_CACHE_SECONDS = 15 * 60
FOREX_FACTORY_US_NEWS_CACHE_SECONDS = 10 * 60
FOREX_FACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
EARNINGS_MANUAL_IMPORT_PATH = ARTIFACTS_DIR / "earnings_manual_imports.json"
EARNINGS_IMAGE_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_OPTION_STOP_LOSS_PCT = 20.0
DEFAULT_OPTION_FIRST_TARGET_PCT = 100.0
DEFAULT_OPTION_RUNNER_LOCK_STEP_PCT = 50.0
DEFAULT_OPTION_ADAPTIVE_SPREAD_PERCENT = 10.0
DEFAULT_OPTION_LIQUIDITY_TARGET_SELL_PCT = 80.0
OPTION_CONTRACT_MULTIPLIER = 100
OPTION_AUTO_ACTIVE_STATUSES = {
    "auto_mode_preview",
    "accepted",
    "accepted_for_bidding",
    "awaiting_approval",
    "exit_pending",
    "manual_exit_pending",
    "new",
    "partially_filled",
    "pending_new",
    "planned",
    "queued",
    "submitted",
    "position_open",
    "open",
}
OPTION_ENTRY_PENDING_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "awaiting_approval",
    "new",
    "partially_filled",
    "pending_new",
    "planned",
    "queued",
    "submitted",
}
OPTION_EXIT_PENDING_STATUSES = {
    "exit_pending",
    "manual_exit_pending",
}
OPTION_LOCAL_ONLY_ARCHIVE_STATUSES = {
    "position_open",
    "open",
    "exit_pending",
    "manual_exit_pending",
}
OPTION_BROKER_OPEN_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
}
OPTION_BROKER_REJECTED_STATUSES = {
    "canceled",
    "expired",
    "rejected",
    "replaced",
    "stopped",
    "suspended",
}
OPTION_ALLOWED_SETUPS = {
    "EMA + VWAP + ORB",
    "EMA + VWAP + Previous Day High",
    "EMA + VWAP + Premarket High",
    "EMA + VWAP",
    "EMA + VWAP + Premarket Low Above Candle",
    "EMA + VWAP + Previous Day Low Above Candle",
}
DEFAULT_OPTION_UNDERLYING_MAP = {
    "AAPU": "AAPL",
    "AMDL": "AMD",
    "AMUU": "AMD",
    "AMZU": "AMZN",
    "ARMU": "ARM",
    "BABX": "BABA",
    "CONL": "COIN",
    "CRWL": "CRWD",
    "GGLL": "GOOGL",
    "IONX": "IONQ",
    "LLYX": "LLY",
    "METU": "META",
    "MSTU": "MSTR",
    "MSTX": "MSTR",
    "MSTY": "MSTR",
    "MULL": "MU",
    "MUU": "MU",
    "NVDL": "NVDA",
    "NVDX": "NVDA",
    "OKLL": "OKLO",
    "PALU": "PANW",
    "PTIR": "PLTR",
    "QBTX": "QBTS",
    "RBLU": "RBLX",
    "RDTL": "RDDT",
    "RGTX": "RGTI",
    "RKLX": "RKLB",
    "SMCX": "SMCI",
    "SNOU": "SNOW",
    "SNXX": "SNDK",
    "SPCU": "SPCX",
    "TEMT": "TEM",
    "TSLL": "TSLA",
    "TSLQ": "TSLA",
    "TSMX": "TSM",
}


def _dedupe_symbol_tokens(raw_items) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        cleaned.append(symbol)
    return cleaned


DEFAULT_MAG7_OPTION_WATCHLIST_SOURCE = _dedupe_symbol_tokens(
    "AAPL AMZN GOOGL META MSFT NFLX NVDA TSLA AVGO INTC AMD AVGO NVDL AMDL METU TSLL SPY QQQ SPCU TQQQ SOXL AMZU USO".split()
)
DEFAULT_MAG7_OPTION_WATCHLIST = _dedupe_symbol_tokens(
    DEFAULT_OPTION_UNDERLYING_MAP.get(symbol, symbol) for symbol in DEFAULT_MAG7_OPTION_WATCHLIST_SOURCE
)
MAGNIFICENT_SEVEN = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]
DEFAULT_OPTION_WATCHLIST = [
    item.strip().upper()
    for item in """
    BILI, CART, BABX, BABA, AAPL, ACHR, BKNG, AMGN, ACB, CCL, AFRM, BULL, AI, ABBV, APLD, BBAI, AMZU, AAOI, ANF, BA, AMZN, AAL, ASTS, AXON, ARMU, BROS, CAVA, APPS, AA, AMC, BLDP, BMNR, CHTR, AEVA, BYND, APP, ABVX, AVGO, AVGX, CCJ, CELH, ABNB, AEM, BUD, CAT, ADBE, CAR, BE, ALAB, BOIL, AMUU, ANET, AMD, CBOE, AMDL, ASML, CHPY, ARM, BTBT, ARQQ, BAC, AMAT, AVAV, AXP, BIDU, BMY, AAP, BB, BBWI, BEAM, CEG, AAPU, LUNR, IONQ, MP, COHR, CRDO, NBIS, CIFR, FLY, LITE, MSTR, MSFT, CRWV, CRCL, CRML, MRVL, CIEN, CLS, NFLX, HD, LASR, IBIT, MU, META, INTC, HPE, DELL, FSLY, DIA, COP, MRNA, GOOGL, JPM, MCD, HOOD, COIN, MARA, CRWD, HUT, IREN, IWM, LAC, NVDA, SMCI, PLTR, ORCL, NOW, SNOW, PANW, XOM, OXY, REGN, WMT, RIOT, RKLB, RGTI, QBTS, QUBT, TSLA, UNH, SLV, USO, OKLO, NVDL, NVDX, NNE, NVTS, PL, QCOM, QQQ, RIVN, RMBS, SMR, SNDK, SPY, STM, STX, TMC, TSM, TSSI, UAMY, USAR, VCX, VIX, VRT, WDC, XOVR, TSLL, METU, GOOG, SPX, HTZ, TLRY, GUSH, PLUG, FCEL, PATH, NAK, RR, KITT, WBD, INOD, SOXS, OUST, F, ONDS, CONL, RDW, MSTU, MSTY, SCHW, QS, UAL, DAL, LUV, ENVX, ENPH, IBM, INFQ, GFS, QMCO, LAES, POET, ZETA, SOFI, CRM, GS, SATS, SPOT, HPQ, SPCE, SEDG, OKTA, M, SCCO, NKE, HSY, RCAT, SHOP, CVX, FSLR, ROKU, CMCSA, COST, DDOG, DIS, KO, NEE, RBLX, SBUX, SNAP, TGT, TMUS, UUUU, CHWY, CSCO, CVNA, CVS, DASH, DHI, EA, EOSE, FCX, FDX, FUBO, GE, GLXY, GME, HIMS, HIVE, JD, JNJ, JOBY, KSS, LCID, LLY, LMND, LOW, LYFT, MDB, MRK, MVIS, NCLH, NIO, OPEN, PEP, PG, PINS, PYPL, RDDT, ROST, RUM, RUN, SGML, SPGI, TEAM, UBER, USB, VZ, WFC, WULF, ZM, ZS, CLF, CLSK, CME, CNQ, CRVW, DECK, DG, DJT, DKNG, DLTR, DOCU, EBAY, ECL, ET, FUTU, GAP, GEV, GILD, GLD, GM, GRAB, HAL, HL, HLT, HSBC, IBKR, INFY, INSM, IOT, IRDM, KGC, KR, KVUE, LHX, LIDR, LMT, LRCX, LVS, MA, MAR, MCHP, MDLZ, MNKD, MO, MPC, MS, MSOS, MSTX, NAIL, NEM, NET, NOC, NTRA, NVAX, ONON, PAAS, PDD, PENN, PFE, PHM, PM, PMH, PNC, PONY, PSX, QXO, RBRK, RTX, RXRX, SBET, SCHD, SHEL, SLB, SNPS, SOUN, SYM, TEM, TJX, TLN, TME, TMO, TOST, TSLQ, TTWO, TXN, UEC, ULTA, UMAC, UPS, UPST, URBN, VKTX, VST, WB, WDAY, WELL, WRBY, XPEV, XYZ, NOK, TQQQ, GGLL, MSFU, SOXL, TSMX, SMCX, MULL, LLYX, CRWL, PALU, PTIR, IONX, RKLX, RGTX, TEMT, SNOU, QBTX, RDTL, RBLU, SPCX, SPCU, DRAM, MUU, NASA, RVI, SNXX, OKLL
    """.replace("\n", " ").split(",")
    if item.strip()
]


SCANNER_HISTORY_PAYLOAD_KEYS = ("scannerHistory", "scannerHistoryDays")
DASHBOARD_COMPACT_OMIT_KEYS = (
    *SCANNER_HISTORY_PAYLOAD_KEYS,
    "catalysts",
    "catalystIndex",
    "optionTradeHistory",
)


def scanner_history_version(history_rows: list, day_rows: list) -> str:
    """Cheap fingerprint of the scanner-history tape.

    The dashboard is polled every 5s and scanner history is 80% of it, but the
    tape only changes when a scan runs. This has to be cheap enough to compute
    on every poll, so it fingerprints shape + newest timestamp rather than
    hashing ~1MB of rows: the tape is an append-only log trimmed by retention,
    so a change always moves the row count, the day count, or the newest
    scanned_at.
    """
    rows = history_rows if isinstance(history_rows, list) else []
    days = day_rows if isinstance(day_rows, list) else []
    newest = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = str(row.get("scanned_at") or row.get("scan_date") or "")
        if stamp > newest:
            newest = stamp
    return f"{len(rows)}:{len(days)}:{newest}"


def browser_scanner_history_records(records: list) -> list:
    """Send stored scanner details as objects, not double-encoded JSON text."""
    normalized = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        raw_json = item.pop("raw_json", None)
        if isinstance(raw_json, str) and raw_json.strip():
            try:
                raw = json.loads(raw_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = None
            if isinstance(raw, dict):
                item["raw"] = raw
        normalized.append(item)
    return normalized


def dashboard_payload_for_client(
    payload: dict,
    client_version: object,
    *,
    compact: bool = False,
) -> dict:
    """Return the smallest dashboard payload needed by this client.

    Returns a shallow copy - `payload` is the shared dashboard cache and is
    handed to every other poller, so it must never be mutated here. Omitting
    the keys (rather than sending []) is deliberate: the browser's
    mergeDashboardPayload spreads `{...current, ...payload}`, so an absent key
    keeps the cached array *and its identity*, which is what stops the
    downstream recompute storm.
    """
    if not isinstance(payload, dict):
        return payload
    omitted_keys = set(DASHBOARD_COMPACT_OMIT_KEYS if compact else ())
    held = str(client_version or "").strip()
    current = str(payload.get("scannerHistoryVersion") or "").strip()
    if held and current and held == current:
        omitted_keys.update(SCANNER_HISTORY_PAYLOAD_KEYS)
    if not any(key in payload for key in omitted_keys):
        return payload
    trimmed = dict(payload)
    for key in omitted_keys:
        trimmed.pop(key, None)
    return trimmed


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty:
        return []

    serializable = frame.copy()
    for column in serializable.columns:
        if pd.api.types.is_datetime64_any_dtype(serializable[column]):
            serializable[column] = serializable[column].dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    return [_serialize_value(record) for record in serializable.to_dict("records")]


def _serialize_value(value):
    if isinstance(value, pd.DataFrame):
        return _frame_records(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _history_summary(frame: pd.DataFrame, today_key: str) -> dict:
    records = [] if frame is None or frame.empty else frame.to_dict("records")
    closed_statuses = {
        "closed", "filled", "exited", "complete", "completed",
        "filled_exit", "closed_or_filled", "cancelled_after_fill",
    }
    pnl_keys = ("pnl", "realized_pnl", "marked_pnl", "unrealized_pnl")
    time_keys = ("exit_time", "closed_at", "entry_time", "created_at", "submitted_at")
    total_pnl = 0.0
    open_pnl = 0.0
    wins = 0
    losses = 0
    closed_trades = 0
    trades_today = 0
    pnl_today = 0.0
    for row in records:
        pnl = 0.0
        for key in pnl_keys:
            value = row.get(key)
            if value not in (None, "") and not pd.isna(value):
                try:
                    pnl = float(value)
                except (TypeError, ValueError):
                    pnl = 0.0
                break
        status = str(row.get("status") or "").strip().lower()
        is_closed = status in closed_statuses or bool(row.get("exit_time") or row.get("closed_at"))
        if is_closed:
            total_pnl += pnl
            closed_trades += 1
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        else:
            open_pnl += pnl
        timestamp = next((row.get(key) for key in time_keys if row.get(key)), None)
        if timestamp and str(timestamp)[:10] == today_key:
            trades_today += 1
            if is_closed:
                pnl_today += pnl
    scored = wins + losses
    return {
        "totalTrades": len(records),
        "closedTrades": closed_trades,
        "tradesToday": trades_today,
        "totalPnL": round(total_pnl, 2),
        "openPnL": round(open_pnl, 2),
        "todayPnL": round(pnl_today, 2),
        "wins": wins,
        "losses": losses,
        "winRate": round((wins / scored) * 100, 2) if scored else 0.0,
    }

class DashboardState:
    def _empty_premarket_plan(self) -> dict:
        return {
            "status": "WARMING",
            "generatedAt": None,
            "message": "Premarket plan is waiting for the first complete OI watchlist cycle.",
            "coverage": {
                "watchlistScanned": 0,
                "watchlistTotal": len(settings.scanner.default_universe),
                "completedCycles": 0,
            },
            "stockCandidates": [],
            "optionCandidates": [],
            "execution": {
                "shadowOnly": True,
                "usesCachedPlanForOrders": False,
                "liveRevalidationRequired": True,
                "wakesOnFreshStockSignal": True,
                "canBlockTrades": False,
                "canRankTrades": False,
                "canSizeTrades": False,
                "canDelayExecution": False,
            },
            "newsPolicy": {
                "informationOnly": True,
                "canBlockTrades": False,
                "canRankTrades": False,
                "canSizeTrades": False,
                "canDelayExecution": False,
            },
        }

    def _empty_option_supervisor_report(self) -> dict:
        return {
            "name": "LLM Supervisor Agent",
            "status": "Waiting For Scan",
            "mode": "advisory_only",
            "updatedAt": None,
            "summary": "Run Option Bot or Scan Underlyings for Options to generate a supervisor review.",
            "authority": {
                "canPlaceOrders": False,
                "canOverrideRules": False,
                "executionPath": "Rules engine only",
                "note": "LLM output is explanation and audit only. Orders still require deterministic option gates.",
            },
            "engineChecklist": [
                "Rules engine handles EMA/VWAP/ORB and approved trigger checks.",
                "Contract agent enforces long calls, delta cap, mid price, bid/ask, spread, and expected move.",
                "Risk agent enforces buying power, quantity, stop, target 1, and runner state.",
                "LLM supervisor explains, flags anomalies, summarizes catalysts, and suggests tuning only.",
            ],
            "taken": [],
            "skipped": [],
            "unusual": ["No option scan has been reviewed yet."],
            "catalysts": [],
            "weakTrades": [],
            "suggestions": ["Start with paper trading only and keep the LLM advisory-only."],
            "settingsSnapshot": {},
            "llm": {
                "mode": "not_started",
                "model": settings.ai.option_llm_supervisor_model,
                "latencyMs": 0,
                "note": "Supervisor has not run yet.",
            },
        }

    def __init__(self) -> None:
        self.repository = TradingRepository()
        self.catalysts = CatalystEngine()
        self.catalyst_refresh_lock = threading.Lock()
        self.catalyst_refresh_thread: threading.Thread | None = None
        self.catalyst_refresh_cursor = 0
        self.catalyst_refresh_batch_size = 40
        self.earnings_calendar_lock = threading.Lock()
        self.earnings_calendar_cache: tuple[datetime, tuple[str, ...], dict] | None = None
        self.earnings_manual_lock = threading.Lock()
        self.earnings_manual_imports = self._load_earnings_manual_imports()
        self.forex_factory_us_news_lock = threading.Lock()
        self.forex_factory_us_news_cache: tuple[datetime, date, dict] | None = None
        self._load_persisted_risk_settings()
        self.active_profile_id = settings.stock_account_profile_id(settings.execution_mode)
        self._rebuild_runtime(self.active_profile_id)
        self.bot_state = "Running" if settings.auto_start_bot else "Stopped"
        self.option_bot_state = getattr(self, "persisted_option_bot_state", "Stopped")
        self.option_bot_message = (
            "Option bot restored as running and will manage stops on the next loop."
            if self.option_bot_state == "Running"
            else "Option planner is idle."
        )
        self.option_scheduler_thread: threading.Thread | None = None
        self.option_scan_wakeup = threading.Event()
        self.stock_entry_wakeup = threading.Event()
        self.premarket_plan_lock = threading.Lock()
        self.premarket_plan: dict = self._empty_premarket_plan()
        self.option_entry_lock = threading.RLock()
        self.option_scheduler_last_run: datetime | None = None
        self.action_message = ""
        self.oi_action_message = ""
        self.scan_results = pd.DataFrame()
        self.candidate_results = pd.DataFrame()
        self.mag7_scan_results = pd.DataFrame()
        self.mag7_candidate_results = pd.DataFrame()
        self.oi_scan_results = pd.DataFrame()
        self.oi_mag7_scan_results = pd.DataFrame()
        self.oi_watchlist_scan_results = pd.DataFrame()
        self.oi_mag7_last_non_empty_results = pd.DataFrame()
        self.oi_watchlist_last_non_empty_results = pd.DataFrame()
        self.option_candidate_results = pd.DataFrame()
        self.option_plan_blocks: list[dict] = []
        self.option_supervisor_report: dict = self._empty_option_supervisor_report()
        self.option_supervisor = OptionLLMSupervisor(
            enabled=settings.ai.option_llm_supervisor_enabled,
            model=settings.ai.option_llm_supervisor_model,
            timeout_seconds=settings.ai.option_llm_supervisor_timeout_seconds,
        )
        self.option_supervisor_refresh_lock = threading.Lock()
        self.option_supervisor_refresh_thread: threading.Thread | None = None
        self.scan_timestamp: datetime | None = None
        self.mag7_scan_timestamp: datetime | None = None
        self.oi_scan_timestamp: datetime | None = None
        self.oi_mag7_scan_timestamp: datetime | None = None
        self.oi_watchlist_scan_timestamp: datetime | None = None
        self.oi_mag7_last_non_empty_timestamp: datetime | None = None
        self.oi_watchlist_last_non_empty_timestamp: datetime | None = None
        self.option_scan_timestamp: datetime | None = None
        self.scan_lock = threading.Lock()
        self.oi_mag7_scan_lock = threading.Lock()
        self.oi_watchlist_scan_lock = threading.Lock()
        self.mag7_oi_wall_lock = threading.Lock()
        self.mag7_oi_wall_cache: dict | None = None
        self.mag7_oi_wall_cache_timestamp: datetime | None = None
        self.oi_finder_lock = threading.RLock()
        self.oi_finder_cache: dict[str, tuple[datetime, dict]] = {}
        # Compact chain payloads for the chart panel's fast path, kept apart
        # from the analytics-bearing Finder cache above.
        self.oi_finder_chain_cache: dict[str, tuple[datetime, dict]] = {}
        self.oi_finder_background_refreshes: set[str] = set()
        self.oi_finder_chain_disk_cache_dir = ARTIFACTS_DIR / "oi_chain_cache"
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_chart_cache: dict[str, dict] = {}
        self.oi_finder_chart_refreshes: set[str] = set()
        self.oi_finder_chart_full_refresh_lock = threading.Lock()
        self.oi_finder_chart_disk_cache_dir = ARTIFACTS_DIR / "oi_chart_cache"
        self.oi_finder_interactive_until = 0.0
        self._oi_finder_mtf_history_cache: dict[str, dict] = {}
        # Each Finder refresh supplies a cumulative option-volume reading. Keep
        # a small in-memory trail so the UI can calculate actual volume change
        # over time for the current ATM and displayed OTM contracts.
        self.oi_finder_volume_history: dict[str, list[dict]] = {}
        # The per-strike volume-rate card keeps one compact cumulative-volume
        # reading per minute for the front expiry.  This is deliberately
        # separate from the short 15-second ROC trail above: it lets the
        # browser subtract cumulative broker volume itself, while avoiding a
        # full option-chain snapshot for every poll.
        self.oi_finder_intraday_volume_history: dict[str, list[dict]] = {}
        # Daily Finder history is intentionally independent from the live
        # scanners.  It takes one sequential chain snapshot per saved ticker
        # after the regular session and never performs a continuous 400-symbol
        # option-chain loop.
        self.oi_finder_snapshot_enabled = bool(settings.scanner.oi_finder_snapshot_enabled)
        self.oi_finder_snapshot_hour_et = min(max(int(settings.scanner.oi_finder_snapshot_hour_et), 16), 23)
        self.oi_finder_snapshot_minute_et = min(max(int(settings.scanner.oi_finder_snapshot_minute_et), 0), 59)
        self.oi_finder_snapshot_interval_seconds = max(
            15,
            int(settings.scanner.oi_finder_snapshot_interval_seconds),
        )
        self.oi_finder_snapshot_thread: threading.Thread | None = None
        self.oi_finder_snapshot_status = "Scheduled" if self.oi_finder_snapshot_enabled else "Disabled"
        self.oi_finder_snapshot_message = "Waiting for the next weekday after-close history snapshot."
        self.oi_finder_snapshot_last_run: datetime | None = None
        self.oi_finder_snapshot_next_run: datetime | None = None
        self.oi_finder_snapshot_last_error = ""
        self.oi_finder_snapshot_progress = {"completed": 0, "total": 0, "failed": 0}
        # Automatic intraday collection is deliberately limited to the saved
        # MAG7 scanner list.  The full saved watchlist continues to be
        # on-demand in OI Finder, preventing a continuous 400-symbol chain
        # sweep while retaining live data immediately after a search.
        self.oi_finder_mag7_live_enabled = bool(settings.scanner.oi_finder_mag7_live_enabled)
        # Keep the collector narrow and sequential, but allow a two-minute
        # Mag7 cadence for premarket option-flow preparation.
        self.oi_finder_mag7_live_interval_seconds = max(120, int(settings.scanner.oi_finder_mag7_live_interval_seconds))
        self.oi_finder_mag7_live_symbol_pause_seconds = max(5.0, float(settings.scanner.oi_finder_mag7_live_symbol_pause_seconds))
        self.oi_finder_mag7_live_thread: threading.Thread | None = None
        self.oi_finder_mag7_live_status = "Starting" if self.oi_finder_mag7_live_enabled else "Disabled"
        self.oi_finder_mag7_live_message = "Preparing MAG7-only live option-volume collection."
        self.oi_finder_mag7_live_last_run: datetime | None = None
        self.oi_finder_mag7_live_next_run: datetime | None = None
        self.oi_finder_mag7_live_last_error = ""
        self.oi_finder_mag7_live_progress = {"completed": 0, "total": 0, "failed": 0}
        self.dashboard_cache_lock = threading.Lock()
        self.dashboard_cache: dict | None = None
        self.dashboard_cache_timestamp: datetime | None = None
        self.dashboard_refresh_thread: threading.Thread | None = None
        self.scan_job = {
            "running": False,
            "message": "No scan running.",
            "startedAt": None,
            "finishedAt": None,
            "error": "",
        }
        self.oi_scan_job = {
            "running": False,
            "scanLabel": "",
            "symbolCount": 0,
            "message": "No manual OI scan running.",
            "startedAt": None,
            "finishedAt": None,
            "error": "",
        }
        # The broad stock-watchlist loop is opt-in. Keep it off when this app
        # is being used for a manual Schwab/TOS OI Finder workflow.
        self.scanner_auto_enabled = getattr(self, "persisted_stock_scanner_auto_enabled", False)
        self.scanner_auto_interval_seconds = max(5, int(settings.scanner.stock_scanner_auto_interval_seconds))
        self.scanner_auto_deep_batch_size = max(1, int(settings.scanner.stock_scanner_deep_batch_size))
        self.scanner_auto_hot_lane_size = max(
            0,
            min(int(settings.scanner.stock_scanner_hot_lane_size), self.scanner_auto_deep_batch_size),
        )
        self.scanner_auto_watchlist_cursor = 0
        self.scanner_auto_watchlist_batch_start = 0
        self.scanner_auto_watchlist_batch_end = 0
        self.scanner_auto_watchlist_completed_cycles = 0
        self.scanner_auto_thread: threading.Thread | None = None
        self.scanner_auto_status = "Starting" if self.scanner_auto_enabled else "Stopped"
        self.scanner_auto_message = (
            "Background scanner is starting."
            if self.scanner_auto_enabled
            else "Background stock watchlist scanner is stopped."
        )
        self.scanner_auto_last_run: datetime | None = None
        self.scanner_auto_next_run: datetime | None = None
        self.scanner_auto_last_error = ""
        # OI scans can make a large number of broker option-chain requests.
        # Keep them opt-in after a restart; OI Finder remains available for
        # explicit single-ticker searches.
        self.oi_scanner_auto_enabled = getattr(self, "persisted_oi_scanner_auto_enabled", False)
        # OI scans are deliberately narrow and slow: one direct Schwab/TOS
        # option-chain pass across the canonical seven Mag7 symbols at most
        # once per minute.  This keeps the automatic scanner from behaving
        # like a broad watchlist scraper.
        self.oi_mag7_auto_interval_seconds = max(60, int(settings.scanner.oi_mag7_auto_interval_seconds))
        self.oi_mag7_continuous_pause_seconds = max(60.0, float(settings.scanner.oi_mag7_continuous_pause_seconds))
        self.oi_watchlist_auto_interval_seconds = max(5, int(settings.scanner.oi_watchlist_auto_interval_seconds))
        self.oi_watchlist_batch_size = max(1, int(settings.scanner.oi_watchlist_batch_size))
        self.oi_watchlist_worker_count = max(1, min(int(settings.scanner.oi_watchlist_worker_count), 8))
        self.oi_watchlist_results_lock = threading.Lock()
        self.oi_watchlist_continuous_pause_seconds = max(
            0.0,
            float(settings.scanner.oi_watchlist_continuous_pause_seconds),
        )
        self.oi_watchlist_batch_cursor = 0
        self.oi_watchlist_batch_start = 0
        self.oi_watchlist_batch_end = 0
        self.oi_watchlist_universe_count = 0
        self.oi_watchlist_completed_cycles = 0
        self.oi_watchlist_cycle_started_at: datetime | None = None
        self.oi_watchlist_cycle_duration_seconds = 0.0
        self.oi_watchlist_batches_completed = 0
        self.oi_watchlist_batch_count = 0
        self.oi_scanner_auto_interval_seconds = self.oi_mag7_auto_interval_seconds
        self.oi_mag7_auto_thread: threading.Thread | None = None
        self.oi_watchlist_auto_thread: threading.Thread | None = None
        self.oi_scanner_auto_status = "Starting" if self.oi_scanner_auto_enabled else "Stopped"
        self.oi_scanner_auto_message = (
            "MAG7-watchlist OI scanner is starting with direct Schwab/TOS option chains."
            if self.oi_scanner_auto_enabled
            else "MAG7-watchlist OI scanner is stopped. OI Finder is available for manual ticker searches."
        )
        self.oi_scanner_auto_last_run: datetime | None = None
        self.oi_scanner_auto_next_run: datetime | None = None
        self.oi_scanner_auto_last_error = ""
        self.oi_mag7_auto_status = "Starting" if self.oi_scanner_auto_enabled else "Stopped"
        self.oi_mag7_auto_last_run: datetime | None = None
        self.oi_mag7_auto_next_run: datetime | None = None
        self.oi_mag7_auto_last_error = ""
        self.oi_mag7_manual_priority_event = threading.Event()
        self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
        self.oi_watchlist_auto_last_run: datetime | None = None
        self.oi_watchlist_auto_next_run: datetime | None = None
        self.oi_watchlist_auto_last_error = ""
        self.oi_watchlist_manual_priority_event = threading.Event()
        self.backtest_summary = pd.DataFrame()
        self.backtest_trades = pd.DataFrame()
        self.backtest_job = {
            "running": False,
            "message": "No backtest running.",
            "symbols": [],
            "startDate": None,
            "endDate": None,
            "startedAt": None,
            "finishedAt": None,
            "error": "",
        }
        self.scheduler_enabled = settings.auto_start_bot
        self.scheduler_interval_seconds = max(60, int(settings.scanner.loop_seconds or 60))
        self.option_scheduler_interval_seconds = 5
        self.stock_position_manager_interval_seconds = 5
        self.stock_position_manager_thread: threading.Thread | None = None
        self.stock_position_manager_status = "Starting"
        self.stock_position_manager_last_run: datetime | None = None
        self.stock_position_manager_last_error = ""
        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_last_run: datetime | None = None
        self.scheduler_next_run: datetime | None = None
        self.scheduler_cycle_count = 0
        self.scheduler_cycle_status = "Booting" if self.scheduler_enabled else "Idle"
        self.scheduler_cycle_message = "Automation loop is preparing to start." if self.scheduler_enabled else "Automation loop is idle."
        self.scheduler_last_error = ""
        self.entry_block_status = ""
        self.entry_block_message = ""
        self.learning_agent = TradingLearningAgent(
            self.repository,
            self.market_data_client,
            symbol_cohort_provider=self._learning_symbol_cohorts,
        )
        self.learning_interval_seconds = 300
        self.learning_thread: threading.Thread | None = None
        self.learning_cycle_thread: threading.Thread | None = None
        self.learning_last_result: dict = {}
        self.learning_status_cache_lock = threading.Lock()
        self.learning_status_cache: dict | None = None
        self.learning_status_cache_timestamp: datetime | None = None
        self.runtime_watchdog_enabled = bool(settings.runtime_watchdog_enabled)
        self.runtime_watchdog_auto_recover = bool(settings.runtime_watchdog_auto_recover)
        self.runtime_watchdog_interval_seconds = max(int(settings.runtime_watchdog_interval_seconds), 5)
        self.runtime_watchdog_stale_multiplier = max(float(settings.runtime_watchdog_stale_multiplier), 2.0)
        self.runtime_watchdog_thread: threading.Thread | None = None
        self.runtime_watchdog_status = "Starting" if self.runtime_watchdog_enabled else "Disabled"
        self.runtime_watchdog_last_run: datetime | None = None
        self.runtime_watchdog_last_error = ""
        self.runtime_watchdog_components: dict[str, dict] = {}
        self.runtime_watchdog_incidents = 0
        self.runtime_watchdog_recoveries = 0
        self.runtime_watchdog_last_incident_signature = ""
        # Do not hold the API port hostage on a full Learning Lab aggregation.
        # The background learning loop refreshes the real snapshot shortly
        # after boot; this small placeholder keeps dashboard reads responsive
        # while the trading engines are brought online.
        self.learning_status_cache = {
            "mode": "advisory_only",
            "phase": "Warming",
            "message": "Learning metrics are loading in the background.",
        }
        self.learning_status_cache_timestamp = datetime.now().astimezone()
        # This restores dashboard-only data from SQLite.  Do not make server
        # availability or the trading engines wait for historical JSON parsing.
        threading.Thread(
            target=self._restore_today_oi_results,
            name="oi-result-restore",
            daemon=True,
        ).start()
        # Seed a non-blocking dashboard immediately. Broker/account enrichment
        # is always refreshed in the background, so UI polling can never hold
        # the execution path or wait on a slow external account.
        self.dashboard_cache = self._dashboard_payload_minimal()
        self.dashboard_cache_timestamp = datetime.now().astimezone() - timedelta(seconds=10)
        if self.scheduler_enabled:
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
            self.repository.log_bot_event("bot_state", f"{settings.account_label} bot auto-started on boot.")
        if self.option_bot_state == "Running":
            self._start_option_scheduler()
            self.repository.log_bot_event("option_bot_state", "Option bot restored as running on backend boot.")
        self._start_scanner_auto_loop()
        self._start_oi_scanner_auto_loops(initial_delay_seconds=60.0)
        self._start_oi_finder_snapshot_schedule()
        self._start_oi_finder_mag7_live_collector()
        self._start_stock_position_manager()
        self._start_learning_loop()
        self._start_runtime_watchdog()

    def _load_persisted_risk_settings(self) -> None:
        persisted = self.repository.get_app_settings()
        if "daily_trade_amount" in persisted:
            settings.trading.daily_trade_amount = max(float(persisted["daily_trade_amount"]), 1.0)
        if "trade_amount" in persisted:
            settings.trading.fixed_trade_amount = max(float(persisted["trade_amount"]), 1.0)
        if "stop_loss_percent" in persisted:
            settings.trading.stop_loss_percent = max(float(persisted["stop_loss_percent"]), 0.01)
        if "stop_loss_amount" in persisted:
            settings.trading.stop_loss_amount = max(float(persisted["stop_loss_amount"]), 0.01)
        if "take_profit_1_pct" in persisted:
            settings.trading.take_profit_1_pct = max(float(persisted["take_profit_1_pct"]), 0.01)
        if "scanner_history_retention_days" in persisted:
            settings.scanner.history_retention_days = max(int(float(persisted["scanner_history_retention_days"])), 1)
        self.option_risk_settings = {
            "dailyTradeAmount": persisted.get("option_daily_trade_amount", ""),
            "tradeAmount": persisted.get("option_trade_amount", ""),
            "contractQuantity": persisted.get("option_contract_quantity", ""),
            "stopLossPercent": persisted.get("option_stop_loss_percent", ""),
            "firstProfitTargetPercent": persisted.get("option_first_profit_target_percent", ""),
            "firstProfitTargetCons": persisted.get("option_first_profit_target_cons", ""),
            "firstProfitTargetSellMode": persisted.get("option_first_profit_target_sell_mode", ""),
            "firstProfitTargetSellValue": persisted.get("option_first_profit_target_sell_value", ""),
            "runnerLockStepPercent": persisted.get("option_runner_lock_step_percent", ""),
        }
        if not self.option_risk_settings["firstProfitTargetSellMode"]:
            target_1_sell_setting = self.option_risk_settings["firstProfitTargetCons"]
            self.option_risk_settings["firstProfitTargetSellMode"] = "percentage" if "%" in target_1_sell_setting else "contracts"
        if not self.option_risk_settings["firstProfitTargetSellValue"]:
            target_1_match = re.search(r"-?\d+(?:\.\d+)?", self.option_risk_settings["firstProfitTargetCons"])
            self.option_risk_settings["firstProfitTargetSellValue"] = target_1_match.group(0) if target_1_match else ""
        self.option_bot_config = {
            "contractPolicy": persisted.get("option_contract_policy", "only_long_call") or "only_long_call",
            "approvalMode": persisted.get("option_approval_mode", "automatic") or "automatic",
            "spreadFilter": persisted.get("option_spread_filter", ""),
            "deltaTarget": persisted.get("option_delta_target", ""),
            "expectedMove": persisted.get("option_expected_move", "") or ">=2",
            "watchlistSource": persisted.get("option_watchlist_source", "option") or "option",
        }
        persisted_option_state = str(persisted.get("option_bot_state", "Stopped") or "Stopped").strip().title()
        self.persisted_option_bot_state = persisted_option_state if persisted_option_state in {"Running", "Paused", "Stopped"} else "Stopped"
        persisted_stock_auto = str(persisted.get("stock_scanner_auto_enabled", "false") or "false").strip().lower()
        self.persisted_stock_scanner_auto_enabled = persisted_stock_auto in {"1", "true", "yes", "on"}
        persisted_oi_auto = str(persisted.get("oi_scanner_auto_enabled", "false") or "false").strip().lower()
        self.persisted_oi_scanner_auto_enabled = persisted_oi_auto in {"1", "true", "yes", "on"}
        raw_option_watchlist = persisted.get("option_watchlist", "")
        self.option_watchlist = self._normalize_option_watchlist(
            raw_option_watchlist.replace("\n", ",").split(",") if raw_option_watchlist else DEFAULT_OPTION_WATCHLIST
        )
        raw_mag7_scanner_watchlist = persisted.get("mag7_scanner_watchlist", "")
        self.mag7_scanner_watchlist = self._normalize_watchlist(
            raw_mag7_scanner_watchlist.replace("\n", ",").split(",")
            if raw_mag7_scanner_watchlist
            else DEFAULT_MAG7_OPTION_WATCHLIST_SOURCE
        )

    def _rebuild_runtime(self, profile_id: str | None = None) -> None:
        requested_profile = (profile_id or settings.stock_account_profile_id(settings.execution_mode) or "default-paper").lower()
        if settings.is_option_account_profile(requested_profile, settings.execution_mode):
            requested_profile = settings.stock_account_profile_id(settings.execution_mode)
        self.active_profile_id = requested_profile
        self.client = AlpacaClient(profile_id=self.active_profile_id)
        self.client.ensure_streaming(settings.scanner.default_universe + ["SPY"])
        self.market_data_client = create_market_data_client(self.client)
        self.scanner = MomentumScanner(client=self.market_data_client)
        self.trader = AlpacaPaperTrader(
            client=self.client,
            scanner=self.scanner,
            repository=self.repository,
        )
        self.backtester = Backtester(client=self.market_data_client)
        self.option_contexts: dict[str, dict] = {}
        self.option_context_lock = threading.RLock()
        for option_profile_id in sorted(settings.option_account_profile_ids("paper")):
            credentials = settings.credentials_for_profile(option_profile_id, "paper")
            option_client = AlpacaClient(profile_id=option_profile_id)
            self.option_contexts[option_profile_id] = {
                "profile_id": option_profile_id,
                "credentials": credentials,
                "client": option_client,
                "trader": AlpacaPaperTrader(
                    client=option_client,
                    scanner=self.scanner,
                    repository=self.repository,
                ),
            }
        self.option_profile_id = settings.option_account_profile_id("paper")
        primary_option_context = self.option_contexts[self.option_profile_id]
        self.option_credentials = primary_option_context["credentials"]
        self.option_client = primary_option_context["client"]
        self.option_trader = primary_option_context["trader"]
        if hasattr(self, "learning_agent"):
            self.learning_agent.market_client = self.market_data_client

    def available_accounts(self) -> list[dict]:
        return [
            {
                "id": profile.profile_id,
                "label": profile.label,
                "tradeLabel": settings.trade_label_for_profile(profile.profile_id),
                "mode": "paper" if profile.paper else "live",
                "paper": profile.paper,
                "active": profile.profile_id.lower() == self.active_profile_id.lower(),
                "optionActive": profile.profile_id.lower() in settings.option_account_profile_ids(self.client.mode),
                "stockTradingDisabled": settings.is_option_account_profile(profile.profile_id, self.client.mode),
                "usage": self._account_usage(profile.profile_id),
            }
            for profile in settings.available_profiles(self.client.mode)
        ]

    def _option_account_credentials(self):
        credentials = getattr(self, "option_credentials", None)
        if credentials is not None:
            return credentials
        return self.client.credentials

    def _option_account_profile_id(self) -> str:
        return str(self._option_account_credentials().profile_id or "").strip().lower()

    def _account_usage(self, profile_id: str | None) -> str:
        requested = str(profile_id or "").strip().lower()
        if requested == settings.option_account_profile_id("paper"):
            return "mag7_options_only"
        if requested == settings.watchlist_option_account_profile_id("paper"):
            return "watchlist_options_only"
        return "stocks"

    def _option_profile_for_underlying(self, underlying_symbol: str | None) -> str:
        symbol = self._normalize_option_symbol(underlying_symbol)
        if symbol in set(self._mag7_option_underlyings()):
            return settings.option_account_profile_id("paper")
        return settings.watchlist_option_account_profile_id("paper")

    def _option_context_for_underlying(self, underlying_symbol: str | None) -> dict:
        profile_id = self._option_profile_for_underlying(underlying_symbol)
        contexts = getattr(self, "option_contexts", None) or {}
        if contexts:
            return contexts.get(profile_id) or contexts[self.option_profile_id]
        return {
            "profile_id": getattr(self, "option_profile_id", self._option_account_profile_id()),
            "credentials": self._option_account_credentials(),
            "client": self.option_client,
            "trader": self.option_trader,
        }

    def _option_context_for_profile(self, profile_id: str | None) -> dict:
        requested = str(profile_id or "").strip().lower()
        contexts = getattr(self, "option_contexts", None) or {}
        if contexts:
            return contexts.get(requested) or contexts[self.option_profile_id]
        return {
            "profile_id": getattr(self, "option_profile_id", self._option_account_profile_id()),
            "credentials": self._option_account_credentials(),
            "client": self.option_client,
            "trader": self.option_trader,
        }

    def _run_in_option_context(self, profile_id: str, callback):
        context = self._option_context_for_profile(profile_id)
        lock = getattr(self, "option_context_lock", None) or threading.RLock()
        with lock:
            original = (
                getattr(self, "option_profile_id", context["profile_id"]),
                getattr(self, "option_credentials", context["credentials"]),
                self.option_client,
                self.option_trader,
            )
            try:
                self.option_profile_id = context["profile_id"]
                self.option_credentials = context["credentials"]
                self.option_client = context["client"]
                self.option_trader = context["trader"]
                return callback()
            finally:
                self.option_profile_id, self.option_credentials, self.option_client, self.option_trader = original

    def _safe_float(self, value, fallback: float = 0.0) -> float:
        fallback_value = 0.0 if fallback is None else fallback
        try:
            if value is None:
                return float(fallback_value)
            text = str(value).strip()
            if not text or text.lower() == "none":
                return float(fallback_value)
            numeric = float(value)
            if pd.isna(numeric):
                return float(fallback_value)
            return numeric
        except Exception:
            return float(fallback_value)

    def _option_order_cost(self, entry_price: float, quantity: float) -> float:
        return round(max(float(entry_price or 0.0), 0.0) * max(float(quantity or 0.0), 0.0) * OPTION_CONTRACT_MULTIPLIER, 2)

    def _occ_underlying(self, option_symbol: str) -> str:
        normalized = self.option_client.normalize_option_symbol(option_symbol)
        match = re.match(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$", normalized)
        return match.group(1) if match else normalized

    def _option_client_order_id(self, underlying_symbol: str, prefix: str = "option") -> str:
        normalized = self._normalize_option_symbol(underlying_symbol) or "UNKNOWN"
        return f"{prefix}-{normalized}-{uuid4().hex[:12]}"

    def _option_tradeable_buying_power(self, account) -> float:
        return self._safe_float(
            getattr(account, "options_buying_power", None),
            self._safe_float(getattr(account, "buying_power", None), 0.0),
        )

    def _option_account_gate(self, order_cost: float = 0.0, option_client=None) -> tuple[object | None, str]:
        selected_client = option_client or self.option_client
        try:
            account = selected_client.get_account()
        except Exception as exc:
            return None, f"unable to reach Alpaca option account: {exc}"

        raw_status = str(getattr(account, "status", "") or "").strip()
        status = raw_status.split(".")[-1].upper() if raw_status else ""
        if status and status != "ACTIVE":
            return account, f"option account status is {status}"
        if bool(getattr(account, "account_blocked", False)):
            return account, "option account is blocked at Alpaca"
        if bool(getattr(account, "trading_blocked", False)):
            return account, "option trading is blocked at Alpaca"
        if bool(getattr(account, "trade_suspended_by_user", False)):
            return account, "option trading is suspended by user at Alpaca"

        buying_power = self._option_tradeable_buying_power(account)
        if buying_power <= 0:
            return account, "option account has $0.00 buying power"
        if order_cost > 0 and buying_power + 0.01 < order_cost:
            return account, f"option account buying power ${buying_power:.2f} is below order cost ${order_cost:.2f}"
        return account, ""

    def _account_status_payload(self, status) -> dict:
        return {
            "accountEquity": float(status.account_equity),
            "cash": float(status.cash),
            "lastEquity": float(status.last_equity),
            "dailyChange": float(status.daily_change),
            "dailyChangePct": float(status.daily_change_pct),
            "buyingPower": float(status.buying_power),
            "dailyPnL": float(status.daily_pnl),
            "tradesToday": int(status.trades_today),
            "openPositions": int(status.open_positions),
            "openOrders": int(status.open_orders),
            "accountMode": status.account_mode,
        }

    def _option_account_status_payload(self, option_client=None, profile_id: str | None = None) -> dict:
        selected_client = option_client or self.option_client
        selected_profile_id = str(profile_id or self._option_account_profile_id()).strip().lower()
        try:
            account = selected_client.get_account()
        except Exception as exc:
            return {
                "accountEquity": 0.0,
                "cash": 0.0,
                "lastEquity": 0.0,
                "dailyChange": 0.0,
                "dailyChangePct": 0.0,
                "buyingPower": 0.0,
                "optionsBuyingPower": 0.0,
                "tradeableBuyingPower": 0.0,
                "dailyPnL": 0.0,
                "tradesToday": 0,
                "openPositions": 0,
                "openOrders": 0,
                "accountMode": "paper",
                "connectionStatus": "Error",
                "statusMessage": str(exc),
            }

        positions = []
        open_orders = []
        status_messages: list[str] = []
        try:
            positions = selected_client.get_option_positions()
        except Exception as exc:
            status_messages.append(f"positions unavailable: {exc}")
        try:
            open_orders = selected_client.get_option_orders(status=QueryOrderStatus.OPEN, limit=200)
        except Exception as exc:
            status_messages.append(f"orders unavailable: {exc}")

        equity = self._safe_float(getattr(account, "equity", None), 0.0)
        last_equity = self._safe_float(getattr(account, "last_equity", None), equity)
        daily_change = round(equity - last_equity, 2)
        daily_change_pct = round((daily_change / last_equity) * 100, 2) if last_equity else 0.0
        intraday_pnl = round(
            sum(self._safe_float(getattr(position, "unrealized_intraday_pl", None), 0.0) for position in positions),
            2,
        )
        buying_power = self._safe_float(getattr(account, "buying_power", None), 0.0)
        options_buying_power = self._option_tradeable_buying_power(account)
        return {
            "accountNumber": str(getattr(account, "account_number", "") or ""),
            "accountEquity": equity,
            "cash": self._safe_float(getattr(account, "cash", None), 0.0),
            "lastEquity": last_equity,
            "dailyChange": daily_change,
            "dailyChangePct": daily_change_pct,
            "buyingPower": buying_power,
            "optionsBuyingPower": options_buying_power,
            "tradeableBuyingPower": options_buying_power,
            "dailyPnL": intraday_pnl,
            "tradesToday": int(
                self.repository.option_trades_today_count(
                    profile_id=selected_profile_id,
                    broker_only=True,
                )
            ),
            "openPositions": len(positions),
            "openOrders": len(open_orders),
            "accountMode": "paper" if selected_client.is_paper else "live",
            "connectionStatus": "Connected" if not status_messages else "Partial",
            "statusMessage": "; ".join(status_messages),
        }

    def _option_account_payload(self, context: dict | None = None) -> dict:
        selected_context = context or self._option_context_for_profile(self.option_profile_id)
        credentials = selected_context["credentials"]
        return {
            "id": credentials.profile_id,
            "label": credentials.label,
            "tradeLabel": settings.trade_label_for_profile(credentials.profile_id),
            "mode": "paper" if credentials.paper else "live",
            "paper": credentials.paper,
            "stockTradingDisabled": True,
            "usage": self._account_usage(credentials.profile_id),
            "note": "Reserved for option paper trading. Stock trading is blocked on this account.",
            "status": self._option_account_status_payload(selected_context["client"], credentials.profile_id),
        }

    def _option_accounts_payload(self) -> list[dict]:
        contexts = getattr(self, "option_contexts", None) or {}
        return [self._option_account_payload(context) for context in contexts.values()] or [self._option_account_payload()]

    def _dashboard_account_books(
        self,
        today_key: str,
        active_stock_status,
        option_accounts_payload: list[dict],
    ) -> list[dict]:
        option_accounts = {str(item.get("id") or "").lower(): item for item in option_accounts_payload}
        books: list[dict] = []
        for account in self.available_accounts():
            profile_id = str(account.get("id") or "").strip().lower()
            is_option = bool(account.get("optionActive"))
            if is_option:
                history = self._enrich_option_trade_history(
                    self.repository.get_option_trade_history(
                        limit=500,
                        profile_id=profile_id,
                        broker_only=True,
                    )
                )
                account_status = (option_accounts.get(profile_id) or {}).get("status", {})
                equity = float(account_status.get("accountEquity") or 0)
                buying_power = float(account_status.get("tradeableBuyingPower") or 0)
                daily_pnl = float(account_status.get("dailyPnL") or 0)
                open_positions = int(account_status.get("openPositions") or 0)
                open_orders = int(account_status.get("openOrders") or 0)
                connection_status = account_status.get("connectionStatus") or "Unavailable"
                status_message = account_status.get("statusMessage") or ""
                bot_state = self.option_bot_state
            else:
                history = self._enrich_trade_history(
                    self.repository.get_trade_history(limit=500, profile_id=profile_id)
                )
                if profile_id == self.active_profile_id.lower():
                    profile_status = active_stock_status
                    connection_status = "Connected"
                    status_message = ""
                else:
                    try:
                        profile_client = AlpacaClient(profile_id=profile_id)
                        profile_trader = AlpacaPaperTrader(
                            client=profile_client,
                            scanner=self.scanner,
                            repository=self.repository,
                        )
                        profile_status = profile_trader.get_status()
                        connection_status = "Connected"
                        status_message = ""
                    except Exception as exc:
                        profile_status = None
                        connection_status = "Error"
                        status_message = str(exc)
                equity = float(getattr(profile_status, "account_equity", 0) or 0)
                buying_power = float(getattr(profile_status, "buying_power", 0) or 0)
                daily_pnl = float(getattr(profile_status, "daily_pnl", 0) or 0)
                open_positions = int(getattr(profile_status, "open_positions", 0) or 0)
                open_orders = int(getattr(profile_status, "open_orders", 0) or 0)
                bot_state = self.bot_state

            book = _history_summary(history, today_key)
            book.update({
                "id": profile_id,
                "label": account.get("label") or profile_id,
                "tradeLabel": account.get("tradeLabel") or account.get("label") or profile_id,
                "usage": account.get("usage") or ("options" if is_option else "stocks"),
                "product": "option" if is_option else "stock",
                "active": bool(account.get("active")),
                "equity": round(equity, 2),
                "buyingPower": round(buying_power, 2),
                "dailyPnL": round(daily_pnl, 2),
                "openPositions": open_positions,
                "openOrders": open_orders,
                "botState": bot_state,
                "connectionStatus": connection_status,
                "statusMessage": status_message,
            })
            books.append(book)
        return books

    def _sync_all_option_broker_states(self) -> dict:
        combined = {"positions": [], "position_map": {}, "open_orders": [], "order_map": {}, "open_sell_symbols": set()}
        with self.option_context_lock:
            original = (self.option_profile_id, self.option_credentials, self.option_client, self.option_trader)
            try:
                for profile_id, context in self.option_contexts.items():
                    self.option_profile_id = profile_id
                    self.option_credentials = context["credentials"]
                    self.option_client = context["client"]
                    self.option_trader = context["trader"]
                    snapshot = self._sync_option_broker_state()
                    combined["positions"].extend(snapshot.get("positions") or [])
                    combined["position_map"].update(snapshot.get("position_map") or {})
                    combined["open_orders"].extend(snapshot.get("open_orders") or [])
                    combined["order_map"].update(snapshot.get("order_map") or {})
                    combined["open_sell_symbols"].update(snapshot.get("open_sell_symbols") or set())
            finally:
                self.option_profile_id, self.option_credentials, self.option_client, self.option_trader = original
        return combined

    def _normalize_watchlist(self, symbols: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = str(raw or "").strip().upper()
            if not symbol:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            cleaned.append(symbol)
        return cleaned

    def _normalize_option_symbol(self, symbol: str) -> str:
        cleaned = str(symbol or "").strip().upper()
        if not cleaned:
            return ""
        return DEFAULT_OPTION_UNDERLYING_MAP.get(cleaned, cleaned)

    def _normalize_option_watchlist(self, symbols: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = self._normalize_option_symbol(raw)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            cleaned.append(symbol)
        return cleaned

    def _option_alias_map(self) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        for source, underlying in DEFAULT_OPTION_UNDERLYING_MAP.items():
            aliases.setdefault(underlying, []).append(source)
        return {
            symbol: sorted(items)
            for symbol, items in aliases.items()
        }

    def _persist_watchlist(self, symbols: list[str]) -> None:
        normalized = self._normalize_watchlist(symbols)
        if not normalized:
            raise ValueError("Watchlist cannot be empty.")
        Path(WATCHLIST_PATH).write_text("\n".join(normalized) + "\n", encoding="utf-8")
        settings.scanner.default_universe = normalized
        if hasattr(self, "scanner") and self.scanner is not None:
            self.scanner.settings.default_universe = normalized
        if hasattr(self, "client") and self.client is not None:
            self.client.ensure_streaming(normalized + ["SPY"])
        self.earnings_calendar_cache = None
        self.action_message = f"Watchlist updated with {len(normalized)} symbols."
        self.repository.log_bot_event("watchlist_update", self.action_message)

    def replace_watchlist(self, symbols: list[str]) -> dict:
        self._persist_watchlist(symbols)
        self.scan_results = pd.DataFrame()
        self.candidate_results = pd.DataFrame()
        self.scan_timestamp = None
        return self.dashboard_payload()

    def add_watchlist_symbol(self, symbol: str) -> dict:
        normalized = self._normalize_watchlist(settings.scanner.default_universe + [symbol])
        self._persist_watchlist(normalized)
        return self.dashboard_payload()

    def remove_watchlist_symbol(self, symbol: str) -> dict:
        target = str(symbol or "").strip().upper()
        normalized = [item for item in settings.scanner.default_universe if str(item).upper() != target]
        self._persist_watchlist(normalized)
        return self.dashboard_payload()

    def _persist_option_watchlist(self, symbols: list[str]) -> None:
        normalized = self._normalize_option_watchlist(symbols)
        if not normalized:
            raise ValueError("Option watchlist cannot be empty.")
        self.option_watchlist = normalized
        self.repository.set_app_setting("option_watchlist", ",".join(normalized))
        self.action_message = f"Option watchlist updated with {len(normalized)} symbols."
        self.repository.log_bot_event("option_watchlist_update", self.action_message)

    def replace_option_watchlist(self, symbols: list[str]) -> dict:
        self._persist_option_watchlist(symbols)
        return self.dashboard_payload()

    def add_option_watchlist_symbol(self, symbol: str) -> dict:
        normalized = self._normalize_option_watchlist(self.option_watchlist + [symbol])
        self._persist_option_watchlist(normalized)
        return self.dashboard_payload()

    def remove_option_watchlist_symbol(self, symbol: str) -> dict:
        target = self._normalize_option_symbol(symbol)
        normalized = [item for item in self.option_watchlist if str(item).upper() != target]
        self._persist_option_watchlist(normalized)
        return self.dashboard_payload()

    def _mag7_option_underlyings(self) -> list[str]:
        source = getattr(self, "mag7_scanner_watchlist", None) or list(DEFAULT_MAG7_OPTION_WATCHLIST_SOURCE)
        return self._normalize_option_watchlist(source)

    def _mag7_oi_underlyings(self) -> list[str]:
        """Use the saved Mag7 scanner list, mapped to unique option underlyings.

        The OI pass remains narrow: a stock setup must qualify before the
        Schwab/TOS option-chain request is made, and option-chain requests are
        capped at two concurrent calls inside ``scan_option_chain_liquidity``.
        """
        return self._mag7_option_underlyings()

    def _watchlist_oi_underlyings(self) -> list[str]:
        mag7_symbols = set(self._mag7_option_underlyings())
        watchlist_symbols = self._normalize_option_watchlist(self.scanner.settings.default_universe)
        return [symbol for symbol in watchlist_symbols if symbol not in mag7_symbols]

    def _learning_symbol_cohorts(self) -> dict[str, str]:
        cohorts = {symbol: "mag7" for symbol in self._mag7_option_underlyings()}
        cohorts.update({symbol: "watchlist" for symbol in self._watchlist_oi_underlyings()})
        return cohorts

    def _persist_mag7_scanner_watchlist(self, symbols: list[str]) -> None:
        normalized = self._normalize_watchlist(symbols)
        if not normalized:
            raise ValueError("Mag7 scanner watchlist cannot be empty.")
        self.mag7_scanner_watchlist = normalized
        self.repository.set_app_setting("mag7_scanner_watchlist", ",".join(normalized))
        self.action_message = f"Mag7 scanner watchlist updated with {len(normalized)} symbols."
        self.repository.log_bot_event("mag7_scanner_watchlist_update", self.action_message)

    def replace_mag7_scanner_watchlist(self, symbols: list[str]) -> dict:
        self._persist_mag7_scanner_watchlist(symbols)
        self.mag7_scan_results = pd.DataFrame()
        self.mag7_candidate_results = pd.DataFrame()
        self.mag7_scan_timestamp = None
        return self.dashboard_payload()

    def add_mag7_scanner_symbol(self, symbol: str) -> dict:
        normalized = self._normalize_watchlist(self.mag7_scanner_watchlist + [symbol])
        self._persist_mag7_scanner_watchlist(normalized)
        return self.dashboard_payload()

    def remove_mag7_scanner_symbol(self, symbol: str) -> dict:
        target = str(symbol or "").strip().upper()
        normalized = [item for item in self.mag7_scanner_watchlist if str(item).upper() != target]
        self._persist_mag7_scanner_watchlist(normalized)
        return self.dashboard_payload()

    def _option_watchlist_source(self) -> str:
        source = str(self.option_bot_config.get("watchlistSource") or "option").strip().lower()
        return "mag7" if source in {"mag7", "mag7-watchlist", "mag7_watchlist"} else "option"

    def _option_watchlist_source_label(self) -> str:
        return "MAG7-Watchlist Options" if self._option_watchlist_source() == "mag7" else "Option Watchlist"

    def _active_option_watchlist(self) -> list[str]:
        if self._option_watchlist_source() == "mag7":
            return self._mag7_option_underlyings()
        return list(self.option_watchlist)

    def _option_bot_trade_universe(self) -> list[str]:
        # Keep option execution scoped to the saved source. In MAG7 mode this
        # must not silently add the broader option watchlist back into trade
        # eligibility.
        return self._normalize_option_watchlist(self._active_option_watchlist())

    def _option_analysis_payload(self, analysis_overrides: dict | None = None) -> dict:
        payload = {
            "spread_filter": self.option_bot_config.get("spreadFilter", ""),
            "delta_target": self.option_bot_config.get("deltaTarget", ""),
            "expected_move_guardrail": self.option_bot_config.get("expectedMove", ""),
            "contract_quantity": self.option_risk_settings.get("contractQuantity", ""),
            "stop_loss_percent": self.option_risk_settings.get("stopLossPercent", ""),
            "first_profit_target_percent": self.option_risk_settings.get("firstProfitTargetPercent", ""),
            "first_profit_target_cons": self.option_risk_settings.get("firstProfitTargetCons", ""),
            "first_profit_target_sell_mode": self.option_risk_settings.get("firstProfitTargetSellMode", ""),
            "first_profit_target_sell_value": self.option_risk_settings.get("firstProfitTargetSellValue", ""),
            "runner_lock_step_percent": self.option_risk_settings.get("runnerLockStepPercent", ""),
            "approval_mode": self.option_bot_config.get("approvalMode", "human"),
            "contract_policy": self.option_bot_config.get("contractPolicy", "only_long_call"),
            "watchlist_source": self._option_watchlist_source(),
        }
        if analysis_overrides:
            payload.update(analysis_overrides)
        return payload

    def _resolve_option_contract(
        self,
        underlying_symbol: str,
        option_symbol: str = "",
        entry_price: float | None = None,
    ) -> tuple[dict | None, str]:
        supplied_symbol = self.option_client.normalize_option_symbol(option_symbol)
        if supplied_symbol and self.option_client.is_option_symbol(supplied_symbol):
            return (
                {
                    "symbol": supplied_symbol,
                    "source_symbol": option_symbol.strip() if option_symbol else supplied_symbol,
                    "underlying": underlying_symbol,
                    "mid": self._safe_float(entry_price, 0.0),
                    "bid": None,
                    "ask": None,
                    "delta": None,
                    "expected_move": None,
                    "expiry_date": None,
                    "strike_price": None,
                    "spread": None,
                    "spread_percent": None,
                    "days_to_expiration": None,
                },
                "",
            )
        selected_contract, selection_error = self._select_option_contract(underlying_symbol)
        if not selected_contract:
            return None, selection_error
        resolved = dict(selected_contract)
        resolved["symbol"] = self.option_client.normalize_option_symbol(resolved.get("symbol"))
        resolved["source_symbol"] = selected_contract.get("source_symbol") or selected_contract.get("symbol")
        return resolved, ""

    def _record_option_learning_observation(
        self,
        underlying_symbol: str,
        selected_contract: dict,
        analysis_payload: dict,
        option_entry_price: float,
        stop_price: float | None,
        target_price: float | None,
        status: str,
        source: str,
        traded: bool,
        trade_reference: str,
    ) -> None:
        snapshot = {
            **selected_contract,
            **analysis_payload,
            "symbol": underlying_symbol,
            "underlying": underlying_symbol,
            "underlying_price": (
                selected_contract.get("underlying_price")
                or analysis_payload.get("underlying_price")
                or analysis_payload.get("stock_entry")
            ),
            "contract": selected_contract.get("symbol"),
            "option_symbol": selected_contract.get("symbol"),
            "option_entry_price": option_entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "trade_status": status,
        }
        self._record_learning_observations(
            pd.DataFrame([snapshot]),
            source=source,
            product="option",
            traded=traded,
            trade_reference=trade_reference,
        )
    def _submit_option_trade_request(
        self,
        underlying_symbol: str,
        option_symbol: str,
        structure: str,
        quantity: float = 1.0,
        entry_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        max_loss_amount: float | None = None,
        notes: str = "",
        trigger_source: str = "option_ticket_preview",
        status: str | None = None,
        analysis_overrides: dict | None = None,
        submit_to_broker: bool = True,
        selected_contract_override: dict | None = None,
        account_gate_prechecked: bool = False,
    ) -> dict:
        """Serialize entries and reject an already-active OCC contract before submission."""
        entry_lock = getattr(self, "option_entry_lock", None)
        if entry_lock is None:
            entry_lock = threading.RLock()
            self.option_entry_lock = entry_lock

        with entry_lock:
            normalized_underlying = self._normalize_option_symbol(underlying_symbol)
            option_context = self._option_context_for_underlying(normalized_underlying)
            option_client = option_context["client"]
            if submit_to_broker and not self._option_entry_window_open():
                return {
                    "ok": False,
                    "status": "entry_window_closed",
                    "reason": "New option entries are limited to 9:30 AM through 3:44:59 PM ET; monitoring and exits remain active until 4:00 PM ET.",
                    "option_symbol": option_client.normalize_option_symbol(option_symbol or ""),
                }
            requested_contract = (
                (selected_contract_override or {}).get("symbol")
                or option_symbol
                or ""
            )
            normalized_contract = option_client.normalize_option_symbol(requested_contract)
            if (
                submit_to_broker
                and normalized_contract
                and normalized_contract in self._active_option_contracts(
                    profile_id=option_context["profile_id"],
                    option_client=option_client,
                )
            ):
                return {
                    "ok": False,
                    "status": "duplicate_blocked",
                    "reason": f"Active option contract already exists for {normalized_contract}; duplicate entry blocked.",
                    "option_symbol": normalized_contract,
                }

            return self._submit_option_trade_request_unlocked(
                underlying_symbol=underlying_symbol,
                option_symbol=option_symbol,
                structure=structure,
                quantity=quantity,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                max_loss_amount=max_loss_amount,
                notes=notes,
                trigger_source=trigger_source,
                status=status,
                analysis_overrides=analysis_overrides,
                submit_to_broker=submit_to_broker,
                selected_contract_override=selected_contract_override,
                account_gate_prechecked=account_gate_prechecked,
            )

    def _submit_option_trade_request_unlocked(
        self,
        underlying_symbol: str,
        option_symbol: str,
        structure: str,
        quantity: float = 1.0,
        entry_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        max_loss_amount: float | None = None,
        notes: str = "",
        trigger_source: str = "option_ticket_preview",
        status: str | None = None,
        analysis_overrides: dict | None = None,
        submit_to_broker: bool = True,
        selected_contract_override: dict | None = None,
        account_gate_prechecked: bool = False,
    ) -> dict:
        normalized_underlying = self._normalize_option_symbol(underlying_symbol)
        if not normalized_underlying:
            return {"ok": False, "reason": "Underlying symbol is required."}

        option_context = self._option_context_for_underlying(normalized_underlying)
        option_client = option_context["client"]
        option_credentials = option_context["credentials"]
        if selected_contract_override:
            selected_contract = dict(selected_contract_override)
            selected_contract["symbol"] = option_client.normalize_option_symbol(selected_contract.get("symbol"))
            selected_contract["source_symbol"] = selected_contract.get("source_symbol") or option_symbol or selected_contract["symbol"]
            selection_error = ""
        else:
            selected_contract, selection_error = self._resolve_option_contract(
                normalized_underlying,
                option_symbol=option_symbol,
                entry_price=entry_price,
            )
        if not selected_contract:
            return {"ok": False, "reason": selection_error or "No option contract could be resolved."}

        resolved_entry_price = self._safe_float(entry_price, self._safe_float(selected_contract.get("mid"), 0.0))
        if resolved_entry_price <= 0:
            return {"ok": False, "reason": "Resolved option mid price is not available for this contract."}

        requested_quantity = self._safe_float(quantity, 0.0)
        resolved_quantity = max(int(round(requested_quantity)), 1)
        if requested_quantity <= 0:
            resolved_quantity = self._option_contract_quantity(resolved_entry_price)
        resolved_quantity = max(int(resolved_quantity), 1)
        option_cost = self._option_order_cost(resolved_entry_price, resolved_quantity)
        resolved_max_loss = self._safe_float(
            max_loss_amount,
            option_cost,
        )
        analysis_payload = self._option_analysis_payload(analysis_overrides)
        analysis_payload.update(
            {
                "entry_mid": round(resolved_entry_price, 4),
                "current_mid": round(resolved_entry_price, 4),
                "selected_option_symbol": selected_contract["symbol"],
                "selected_option_source_symbol": selected_contract.get("source_symbol"),
                "selected_option_mid": round(
                    self._safe_float(selected_contract.get("mid"), resolved_entry_price),
                    4,
                ),
                "selected_option_bid": selected_contract.get("bid"),
                "selected_option_ask": selected_contract.get("ask"),
                "selected_option_delta": selected_contract.get("delta"),
                "selected_option_expected_move": selected_contract.get("expected_move"),
                "selected_option_expiry": selected_contract.get("expiry_date"),
                "selected_option_strike": selected_contract.get("strike_price"),
                "selected_option_spread": selected_contract.get("spread"),
                "selected_option_spread_percent": selected_contract.get("spread_percent"),
                "selected_option_volume": selected_contract.get("total_volume"),
                "selected_option_open_interest": selected_contract.get("open_interest"),
                "selected_option_liquidity_score": selected_contract.get("liquidity_score"),
                "liquidity_breakout_level": selected_contract.get("liquidity_breakout_level"),
                "liquidity_breakout_passed": selected_contract.get("liquidity_breakout_passed"),
                "liquidity_breakout_required": selected_contract.get("liquidity_breakout_required"),
                "liquidity_atm_volume": selected_contract.get("liquidity_atm_volume"),
                "liquidity_atm_open_interest": selected_contract.get("liquidity_atm_open_interest"),
                "liquidity_atm_score": selected_contract.get("liquidity_atm_score"),
                "liquidity_atm_dominates_otm": selected_contract.get("liquidity_atm_dominates_otm"),
                "underlying_target_1_strike": selected_contract.get("underlying_target_strike"),
                "underlying_target_1_sell_percent": DEFAULT_OPTION_LIQUIDITY_TARGET_SELL_PCT if selected_contract.get("underlying_target_strike") else None,
                "underlying_target_volume": selected_contract.get("underlying_target_volume"),
                "underlying_target_open_interest": selected_contract.get("underlying_target_open_interest"),
                "underlying_target_liquidity_score": selected_contract.get("underlying_target_liquidity_score"),
                "underlying_target_liquidity_metric": selected_contract.get("underlying_target_liquidity_metric"),
                "selected_option_broker_symbol": selected_contract["symbol"],
                "remaining_quantity": float(analysis_payload.get("remaining_quantity") or resolved_quantity),
                "broker_status": "awaiting_submission" if submit_to_broker else "awaiting_approval",
            }
        )
        if stop_price is not None:
            analysis_payload["runner_stop"] = round(float(stop_price), 4)
        if target_price is not None:
            analysis_payload["take_profit_1"] = round(float(target_price), 4)

        client_order_id = self._option_client_order_id(normalized_underlying)
        analysis_payload["account_profile_id"] = option_credentials.profile_id
        analysis_payload["account_label"] = option_credentials.label
        analysis_payload["learning_cohort"] = "mag7" if option_credentials.profile_id == settings.option_account_profile_id("paper") else "watchlist"
        normalized_status = status or ("awaiting_approval" if not submit_to_broker else "submitted")

        if not submit_to_broker:
            self.repository.log_option_trade(
                client_order_id=client_order_id,
                broker_order_id="",
                underlying_symbol=normalized_underlying,
                option_symbol=selected_contract["symbol"],
                account_profile_id=option_credentials.profile_id,
                account_label=option_credentials.label,
                structure=structure or "Only Long Call",
                side="buy_to_open",
                quantity=resolved_quantity,
                entry_price=resolved_entry_price,
                stop_price=stop_price,
                target_price=target_price,
                max_loss_amount=resolved_max_loss,
                approval_mode=self.option_bot_config["approvalMode"],
                status=normalized_status,
                trigger_source=trigger_source,
                analysis_json=json.dumps(analysis_payload, default=str),
                notes=notes,
            )
            self._record_option_learning_observation(
                normalized_underlying,
                selected_contract,
                analysis_payload,
                resolved_entry_price,
                stop_price,
                target_price,
                normalized_status,
                "option_bot_ticket",
                False,
                client_order_id,
            )
            return {
                "ok": True,
                "submitted_to_broker": False,
                "status": normalized_status,
                "client_order_id": client_order_id,
                "option_symbol": selected_contract["symbol"],
                "quantity": resolved_quantity,
            }

        if account_gate_prechecked:
            gate_error = ""
        else:
            _, gate_error = self._option_account_gate(option_cost, option_client=option_client)
        if gate_error:
            analysis_payload["broker_status"] = "rejected"
            analysis_payload["broker_error"] = gate_error
            self.repository.log_option_trade(
                client_order_id=client_order_id,
                broker_order_id="",
                underlying_symbol=normalized_underlying,
                option_symbol=selected_contract["symbol"],
                account_profile_id=option_credentials.profile_id,
                account_label=option_credentials.label,
                structure=structure or "Only Long Call",
                side="buy_to_open",
                quantity=resolved_quantity,
                entry_price=resolved_entry_price,
                stop_price=stop_price,
                target_price=target_price,
                max_loss_amount=resolved_max_loss,
                approval_mode=self.option_bot_config["approvalMode"],
                status="rejected",
                trigger_source=trigger_source,
                analysis_json=json.dumps(analysis_payload, default=str),
                notes=gate_error,
            )
            return {"ok": False, "reason": gate_error, "status": "rejected"}

        broker_submit_started = time.perf_counter()
        try:
            order = option_client.submit_option_limit_order(
                symbol=selected_contract["symbol"],
                qty=resolved_quantity,
                limit_price=resolved_entry_price,
                client_order_id=client_order_id,
                position_intent="buy_to_open",
            )
        except Exception as exc:
            broker_submit_ms = round((time.perf_counter() - broker_submit_started) * 1000.0, 2)
            error_message = f"Alpaca option entry rejected: {exc}"
            analysis_payload["broker_status"] = "rejected"
            analysis_payload["broker_error"] = str(exc)
            analysis_payload["broker_submit_ms"] = broker_submit_ms
            self.repository.log_option_trade(
                client_order_id=client_order_id,
                broker_order_id="",
                underlying_symbol=normalized_underlying,
                option_symbol=selected_contract["symbol"],
                account_profile_id=option_credentials.profile_id,
                account_label=option_credentials.label,
                structure=structure or "Only Long Call",
                side="buy_to_open",
                quantity=resolved_quantity,
                entry_price=resolved_entry_price,
                stop_price=stop_price,
                target_price=target_price,
                max_loss_amount=resolved_max_loss,
                approval_mode=self.option_bot_config["approvalMode"],
                status="rejected",
                trigger_source=trigger_source,
                analysis_json=json.dumps(analysis_payload, default=str),
                notes=error_message,
            )
            return {"ok": False, "reason": error_message, "status": "rejected", "broker_submit_ms": broker_submit_ms}

        broker_submit_ms = round((time.perf_counter() - broker_submit_started) * 1000.0, 2)
        broker_status = str(getattr(order, "status", "submitted") or "submitted").strip().lower()
        filled_entry_price = self._safe_float(getattr(order, "filled_avg_price", None), resolved_entry_price)
        stored_status = "position_open" if broker_status == "filled" else broker_status
        analysis_payload.update(
            {
                "broker_order_id": str(getattr(order, "id", "") or ""),
                "broker_status": broker_status,
                "alpaca_limit_price": self._safe_float(getattr(order, "limit_price", None), resolved_entry_price),
                "alpaca_submitted_at": _serialize_value(getattr(order, "submitted_at", None)),
                "broker_submit_ms": broker_submit_ms,
                "fast_execution_path": account_gate_prechecked,
                "entry_mid": round(filled_entry_price, 4),
                "current_mid": round(filled_entry_price, 4),
                "selected_option_mid": round(filled_entry_price, 4),
                "option_lifecycle": "position_open" if stored_status == "position_open" else "entry_pending",
            }
        )
        self.repository.log_option_trade(
            client_order_id=client_order_id,
            broker_order_id=str(getattr(order, "id", "") or ""),
            underlying_symbol=normalized_underlying,
            option_symbol=selected_contract["symbol"],
            account_profile_id=option_credentials.profile_id,
            account_label=option_credentials.label,
            structure=structure or "Only Long Call",
            side="buy_to_open",
            quantity=resolved_quantity,
            entry_price=filled_entry_price,
            stop_price=stop_price,
            target_price=target_price,
            max_loss_amount=resolved_max_loss,
            approval_mode=self.option_bot_config["approvalMode"],
            status=stored_status,
            trigger_source=trigger_source,
            analysis_json=json.dumps(analysis_payload, default=str),
            notes=notes,
        )
        self._record_option_learning_observation(
            normalized_underlying,
            selected_contract,
            analysis_payload,
            filled_entry_price,
            stop_price,
            target_price,
            stored_status,
            "option_bot_trade",
            True,
            client_order_id,
        )
        return {
            "ok": True,
            "submitted_to_broker": True,
            "status": stored_status,
            "client_order_id": client_order_id,
            "option_symbol": selected_contract["symbol"],
            "quantity": resolved_quantity,
            "broker_order_id": str(getattr(order, "id", "") or ""),
            "broker_submit_ms": broker_submit_ms,
        }

    def log_option_paper_trade(
        self,
        underlying_symbol: str,
        option_symbol: str,
        structure: str,
        quantity: float = 1.0,
        entry_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        max_loss_amount: float | None = None,
        notes: str = "",
        trigger_source: str = "option_ticket_preview",
        status: str | None = None,
        analysis_overrides: dict | None = None,
        submit_to_broker: bool = True,
    ) -> dict:
        result = self._submit_option_trade_request(
            underlying_symbol=underlying_symbol,
            option_symbol=option_symbol,
            structure=structure,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            max_loss_amount=max_loss_amount,
            notes=notes,
            trigger_source=trigger_source,
            status=status,
            analysis_overrides=analysis_overrides,
            submit_to_broker=submit_to_broker,
        )
        normalized_underlying = self._normalize_option_symbol(underlying_symbol)
        if result.get("ok"):
            if result.get("submitted_to_broker"):
                self.action_message = f"Submitted Alpaca paper option order for {normalized_underlying}."
            else:
                self.action_message = f"Queued option approval ticket for {normalized_underlying}."
            self.repository.log_bot_event("option_trade_logged", self.action_message, json.dumps(result, default=str))
        else:
            self.action_message = str(result.get("reason") or f"Option order failed for {normalized_underlying}.")
            self.repository.log_bot_event("option_trade_error", self.action_message, json.dumps(result, default=str))
        return self.dashboard_payload()

    def _active_option_trade_underlyings(self, broker_snapshot: dict | None = None) -> set[str]:
        history = self.repository.get_option_trade_history(limit=2000)
        allowed_profiles = settings.option_account_profile_ids("paper")
        active: set[str] = set()
        if not history.empty:
            for row in history.to_dict("records"):
                if str(row.get("account_profile_id") or "").strip().lower() not in allowed_profiles:
                    continue
                status = str(row.get("status") or "").strip().lower()
                if status in OPTION_AUTO_ACTIVE_STATUSES and not row.get("closed_at"):
                    active.add(str(row.get("underlying_symbol") or "").upper())
        if broker_snapshot:
            for position in broker_snapshot.get("positions") or []:
                option_symbol = self.option_client.normalize_option_symbol(str(getattr(position, "symbol", "") or ""))
                if option_symbol:
                    active.add(self._occ_underlying(option_symbol))
            for order in broker_snapshot.get("open_orders") or []:
                option_symbol = self.option_client.normalize_option_symbol(str(getattr(order, "symbol", "") or ""))
                side = str(getattr(order, "side", "") or "").split(".")[-1].lower()
                status = str(getattr(order, "status", "") or "").split(".")[-1].lower()
                if option_symbol and side == "buy" and status in OPTION_BROKER_OPEN_ORDER_STATUSES:
                    active.add(self._occ_underlying(option_symbol))
        return active

    def _active_option_contracts(self, profile_id: str, option_client=None) -> set[str]:
        """Return active OCC contracts for one account from both journal and broker state."""
        selected_client = option_client or self.option_client
        active: set[str] = set()
        history = self.repository.get_option_trade_history(limit=2000, profile_id=profile_id)
        if not history.empty:
            for row in history.to_dict("records"):
                status = str(row.get("status") or "").strip().lower()
                if status not in OPTION_AUTO_ACTIVE_STATUSES or row.get("closed_at"):
                    continue
                option_symbol = selected_client.normalize_option_symbol(row.get("option_symbol") or "")
                if option_symbol:
                    active.add(option_symbol)

        try:
            for position in selected_client.get_option_positions():
                option_symbol = selected_client.normalize_option_symbol(getattr(position, "symbol", "") or "")
                if option_symbol:
                    active.add(option_symbol)
            for order in selected_client.get_option_orders(status=QueryOrderStatus.OPEN, limit=500):
                option_symbol = selected_client.normalize_option_symbol(getattr(order, "symbol", "") or "")
                side = str(getattr(order, "side", "") or "").split(".")[-1].lower()
                status = str(getattr(order, "status", "") or "").split(".")[-1].lower()
                if option_symbol and side == "buy" and status in OPTION_BROKER_OPEN_ORDER_STATUSES:
                    active.add(option_symbol)
        except Exception:
            # The normal account/buying-power gate reports broker connectivity failures.
            # Journal state still protects filled and locally active contracts here.
            pass
        return active

    def _cancel_stale_option_buy_orders_result(self) -> dict:
        configured_contracts = self._parse_numeric_guardrail(self.option_risk_settings.get("contractQuantity"), None)
        if configured_contracts is None or configured_contracts <= 0:
            return {
                "status": "blocked",
                "message": "Set How Many Contracts Buy before canceling stale option buy orders.",
                "canceled": [],
                "skipped": [],
            }
        desired_qty = max(int(configured_contracts), 1)
        try:
            snapshot = self._option_broker_snapshot()
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Unable to reach Alpaca option account right now: {exc}",
                "canceled": [],
                "skipped": [],
            }

        canceled: list[dict] = []
        skipped: list[dict] = []
        for order in snapshot.get("open_orders") or []:
            option_symbol = self.option_client.normalize_option_symbol(str(getattr(order, "symbol", "") or ""))
            if not option_symbol or not self.option_client.is_option_symbol(option_symbol):
                continue
            side = str(getattr(order, "side", "") or "").split(".")[-1].lower()
            status = str(getattr(order, "status", "") or "").split(".")[-1].lower()
            qty = max(int(round(self._safe_float(getattr(order, "qty", None), 0.0))), 0)
            if side != "buy" or status not in OPTION_BROKER_OPEN_ORDER_STATUSES:
                continue
            if qty == desired_qty:
                skipped.append({"symbol": option_symbol, "qty": qty, "reason": "matches current contract count"})
                continue
            order_id = str(getattr(order, "id", "") or "").strip()
            if not order_id:
                skipped.append({"symbol": option_symbol, "qty": qty, "reason": "missing broker order id"})
                continue
            try:
                self.option_client.cancel_order_by_id(order_id)
            except Exception as exc:
                skipped.append({"symbol": option_symbol, "qty": qty, "reason": f"cancel rejected: {exc}"})
                continue
            canceled.append(
                {
                    "symbol": option_symbol,
                    "underlying": self._occ_underlying(option_symbol),
                    "qty": qty,
                    "orderId": order_id,
                }
            )

        message = (
            f"Canceled {len(canceled)} stale option buy order(s) not matching {desired_qty} contract(s)."
            if canceled
            else f"No stale option buy orders found. Open buy orders already match {desired_qty} contract(s)."
        )
        return {"status": "ok", "message": message, "desiredQty": desired_qty, "canceled": canceled, "skipped": skipped}

    def _option_contract_preview(self, row: dict) -> str:
        symbol = str(row.get("symbol") or "").upper()
        entry = float(row.get("entry") or row.get("entry_price") or 0)
        delta_target = str(self.option_bot_config.get("deltaTarget") or "").strip()
        strike_hint = f"Delta <= {delta_target}" if delta_target else "ATM / 1 ITM"
        entry_hint = f"{entry:.2f}" if entry > 0 else "Entry TBD"
        return f"{symbol} CALL | Next Weekly | {strike_hint} | Underlying {entry_hint}"

    def _parse_numeric_guardrail(self, raw: str | None, default: float | None = None) -> float | None:
        text = str(raw or "").strip()
        if not text:
            return default
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return default
        try:
            return float(match.group(0))
        except ValueError:
            return default

    def _canonical_option_expiry_date(self, value: object) -> str:
        """Normalize Schwab expiry values such as 2026-07-17T20:00:00Z to YYYY-MM-DD."""
        text = str(value or "").strip()
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else text

    def _option_chain_contracts(self, chain_payload: dict, contract_type: str = "CALL") -> list[dict]:
        if not isinstance(chain_payload, dict):
            return []
        map_key = "callExpDateMap" if contract_type.upper() == "CALL" else "putExpDateMap"
        raw_map = chain_payload.get(map_key) or {}
        # This walks every contract in the chain and re-parses ~15 fields per
        # contract out of strings. It is pure for a given (payload, side), yet
        # one payload build calls it ~20 times — and _expected_move_for_expiry
        # calls it twice PER EXPIRY, so a 20-expiry chain re-normalized the
        # same thousands of contracts dozens of times while holding the GIL.
        # Memoize per payload object: keyed by id() with the payload held in
        # the entry so an id cannot be recycled under a live key, FIFO-bounded
        # because only a few chains are ever in flight at once.
        cached_maps = _OPTION_CHAIN_CONTRACT_CACHE.get(id(chain_payload))
        if cached_maps is not None and cached_maps[0] is chain_payload:
            cached_contracts = cached_maps[1].get(map_key)
            if cached_contracts is not None:
                return cached_contracts
        flattened: list[dict] = []
        deduplicated: dict[tuple[str, float, str], dict] = {}
        for expiry_key, strikes in raw_map.items():
            expiry = str(expiry_key).split(":", 1)[0]
            if not isinstance(strikes, dict):
                continue
            for strike_key, contracts in strikes.items():
                if not isinstance(contracts, list):
                    continue
                for contract in contracts:
                    if not isinstance(contract, dict):
                        continue
                    item = dict(contract)
                    item["expiry_date"] = self._canonical_option_expiry_date(item.get("expirationDate") or expiry)
                    item["strike_price"] = self._parse_numeric_guardrail(str(item.get("strikePrice", strike_key)))
                    item["total_volume"] = self._parse_numeric_guardrail(
                        str(item.get("totalVolume", item.get("volume", item.get("total_volume", 0)))),
                        0.0,
                    )
                    item["open_interest"] = self._parse_numeric_guardrail(
                        str(item.get("openInterest", item.get("open_interest", 0))),
                        0.0,
                    )
                    greeks = item.get("greeks") if isinstance(item.get("greeks"), dict) else {}
                    item["delta"] = self._parse_numeric_guardrail(
                        str(item.get("delta", greeks.get("delta", 0))),
                        0.0,
                    )
                    root_gamma = self._parse_numeric_guardrail(str(item.get("gamma", "")), 0.0)
                    nested_gamma = self._parse_numeric_guardrail(str(greeks.get("gamma", "")), 0.0)
                    item["gamma"] = root_gamma if root_gamma != 0 else nested_gamma
                    item["bid"] = self._parse_numeric_guardrail(
                        str(item.get("bid", item.get("bidPrice", 0))),
                        0.0,
                    )
                    item["ask"] = self._parse_numeric_guardrail(
                        str(item.get("ask", item.get("askPrice", 0))),
                        0.0,
                    )
                    item["last"] = self._parse_numeric_guardrail(
                        str(item.get("last", item.get("lastPrice", 0))),
                        0.0,
                    )
                    item["mark"] = self._parse_numeric_guardrail(
                        str(item.get("mark", item.get("markPrice", 0))),
                        0.0,
                    )
                    item["theta"] = self._parse_numeric_guardrail(
                        str(item.get("theta", greeks.get("theta", 0))),
                        0.0,
                    )
                    item["vega"] = self._parse_numeric_guardrail(
                        str(item.get("vega", greeks.get("vega", 0))),
                        0.0,
                    )
                    expiry_date = str(item.get("expiry_date") or "")
                    strike_price = self._safe_float(item.get("strike_price"), 0.0)
                    contract_key = (expiry_date, round(strike_price, 4), str(item.get("symbol") or ""))
                    existing = deduplicated.get(contract_key)
                    candidate_quality = (
                        self._safe_float(item.get("total_volume"), 0.0),
                        self._safe_float(item.get("open_interest"), 0.0),
                        abs(self._safe_float(item.get("gamma"), 0.0)),
                    )
                    existing_quality = (
                        self._safe_float(existing.get("total_volume"), 0.0),
                        self._safe_float(existing.get("open_interest"), 0.0),
                        abs(self._safe_float(existing.get("gamma"), 0.0)),
                    ) if existing else None
                    if existing is None or candidate_quality > existing_quality:
                        deduplicated[contract_key] = item
        flattened.extend(deduplicated.values())
        _remember_option_chain_contracts(chain_payload, map_key, flattened)
        return flattened

    def _option_contract_market_fields(self, contract: dict) -> dict:
        """Keep the quote identity and Greeks required by the live chain UI."""
        bid = self._safe_float(contract.get("bid"), 0.0)
        ask = self._safe_float(contract.get("ask"), 0.0)
        last = self._safe_float(contract.get("last"), 0.0)
        provider_mark = self._safe_float(contract.get("mark"), 0.0)
        mark = provider_mark
        if mark <= 0 and bid > 0 and ask > 0:
            mark = (bid + ask) / 2.0
        if mark <= 0 and last > 0:
            mark = last
        return {
            "symbol": str(contract.get("symbol") or "").strip().upper(),
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "last": round(last, 4),
            "mark": round(mark, 4),
            "gamma": round(abs(self._safe_float(contract.get("gamma"), 0.0)), 6),
            "theta": round(self._safe_float(contract.get("theta"), 0.0), 6),
            "vega": round(self._safe_float(contract.get("vega"), 0.0), 6),
        }

    def _option_underlying_price_from_chain(self, chain_payload: dict) -> float:
        try:
            underlying_price = float(chain_payload.get("underlyingPrice") or 0.0)
        except Exception:
            underlying_price = 0.0
        if underlying_price > 0:
            return underlying_price
        underlying = chain_payload.get("underlying") or {}
        return self._safe_float((underlying or {}).get("last"), 0.0)

    def _option_underlying_day_move(self, chain_payload: dict, quote: dict | None = None) -> dict:
        """Normalize today's underlying price move for the OI Finder header."""
        underlying = chain_payload.get("underlying") or {}
        quote = quote or {}
        price = self._safe_float(
            quote.get("last_price"),
            self._option_underlying_price_from_chain(chain_payload),
        )
        change = self._safe_float(
            quote.get("change"),
            underlying.get("netChange", underlying.get("change")),
        )
        change_pct = self._safe_float(
            quote.get("change_pct"),
            underlying.get("netPercentChange", underlying.get("percentChange")),
        )
        if change == 0.0 and change_pct and price > 0:
            previous_close = price / (1.0 + (change_pct / 100.0))
            change = price - previous_close
        if change_pct == 0.0 and change and price > change:
            previous_close = price - change
            change_pct = (change / previous_close) * 100.0 if previous_close else 0.0
        return {
            "price": round(price, 4) if price else None,
            "change": round(change, 4) if change else 0.0,
            "changePercent": round(change_pct, 4) if change_pct else 0.0,
        }

    def _chain_implied_volatility(self, chain_payload: dict) -> float | None:
        raw_volatility = self._safe_float(chain_payload.get("volatility"), 0.0)
        if raw_volatility <= 0:
            return None
        return raw_volatility / 100.0 if raw_volatility > 1.0 else raw_volatility

    def _oi_finder_atm_contract(self, contracts: list[dict], underlying_price: float) -> dict | None:
        """Use the TOS-style lower strike anchor for the OI Finder's ATM comparison."""
        if not contracts or underlying_price <= 0:
            return None
        lower_or_equal = [
            item for item in contracts
            if self._safe_float(item.get("strike_price"), 0.0) <= underlying_price
        ]
        if lower_or_equal:
            return max(
                lower_or_equal,
                key=lambda item: (
                    self._safe_float(item.get("strike_price"), 0.0),
                    self._option_combined_liquidity_score(item),
                ),
            )
        return min(
            contracts,
            key=lambda item: (
                abs(self._safe_float(item.get("strike_price"), 0.0) - underlying_price),
                -self._option_combined_liquidity_score(item),
            ),
        )

    def _option_liquidity_score(self, contract: dict) -> float:
        volume = self._safe_float(contract.get("total_volume", contract.get("totalVolume", contract.get("volume"))), 0.0)
        open_interest = self._safe_float(contract.get("open_interest", contract.get("openInterest")), 0.0)
        # Manual option selection uses the bigger of volume or OI as the
        # liquidity magnet, with the other metric as confirmation/tie-break.
        return max(max(volume, 0.0), max(open_interest, 0.0))

    def _option_combined_liquidity_score(self, contract: dict) -> float:
        volume = self._safe_float(contract.get("total_volume", contract.get("totalVolume", contract.get("volume"))), 0.0)
        open_interest = self._safe_float(contract.get("open_interest", contract.get("openInterest")), 0.0)
        return max(volume, 0.0) + max(open_interest, 0.0)

    def _option_oi_wall_plan(self, chain_payload: dict, expiry_date: str, atm_strike: float = 0.0) -> dict:
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        if underlying_price <= 0:
            return {}

        calls = [
            item for item in self._option_chain_contracts(chain_payload, "CALL")
            if str(item.get("expiry_date")) == str(expiry_date)
            and self._safe_float(item.get("strike_price"), 0.0) >= max(atm_strike, 0.0)
            and self._safe_float(item.get("open_interest"), 0.0) > 0
        ]
        puts = [
            item for item in self._option_chain_contracts(chain_payload, "PUT")
            if str(item.get("expiry_date")) == str(expiry_date)
            and self._safe_float(item.get("strike_price"), 0.0) <= (atm_strike or underlying_price)
            and self._safe_float(item.get("open_interest"), 0.0) > 0
        ]

        def wall_for(contracts: list[dict]) -> dict:
            if not contracts:
                return {}
            wall = max(
                contracts,
                key=lambda item: (
                    self._safe_float(item.get("open_interest"), 0.0),
                    self._safe_float(item.get("total_volume"), 0.0),
                    -abs(self._safe_float(item.get("strike_price"), 0.0) - underlying_price),
                ),
            )
            oi_values = sorted(self._safe_float(item.get("open_interest"), 0.0) for item in contracts)
            midpoint = len(oi_values) // 2
            median_oi = (
                oi_values[midpoint]
                if len(oi_values) % 2
                else (oi_values[midpoint - 1] + oi_values[midpoint]) / 2.0
            )
            wall_oi = self._safe_float(wall.get("open_interest"), 0.0)
            concentration = wall_oi / max(median_oi, 1.0)
            return {
                "strike": self._safe_float(wall.get("strike_price"), 0.0),
                "open_interest": wall_oi,
                "volume": self._safe_float(wall.get("total_volume"), 0.0),
                "concentration": round(concentration, 2),
                "strength": "STRONG" if concentration >= 3.0 else "MODERATE" if concentration >= 1.5 else "LIGHT",
            }

        call_wall = wall_for(calls)
        put_wall = wall_for(puts)
        call_strike = self._safe_float(call_wall.get("strike"), 0.0)
        put_strike = self._safe_float(put_wall.get("strike"), 0.0)
        call_distance_pct = ((call_strike - underlying_price) / underlying_price) * 100.0 if call_strike else None
        put_distance_pct = ((underlying_price - put_strike) / underlying_price) * 100.0 if put_strike else None

        if call_strike and underlying_price >= call_strike:
            wall_signal = "CALL WALL BREAK"
        elif call_distance_pct is not None and call_distance_pct <= 1.0:
            wall_signal = "TESTING CALL WALL"
        elif call_distance_pct is not None and call_distance_pct <= 3.0:
            wall_signal = "APPROACHING CALL WALL"
        elif call_strike and put_strike:
            wall_signal = "BETWEEN OI WALLS"
        elif call_strike:
            wall_signal = "CALL WALL AHEAD"
        elif put_strike:
            wall_signal = "PUT WALL SUPPORT"
        else:
            wall_signal = "NO OI WALL"

        return {
            "call_wall_strike": call_strike or None,
            "call_wall_open_interest": call_wall.get("open_interest"),
            "call_wall_volume": call_wall.get("volume"),
            "call_wall_concentration": call_wall.get("concentration"),
            "call_wall_strength": call_wall.get("strength"),
            "call_wall_distance_pct": round(call_distance_pct, 2) if call_distance_pct is not None else None,
            "put_wall_strike": put_strike or None,
            "put_wall_open_interest": put_wall.get("open_interest"),
            "put_wall_volume": put_wall.get("volume"),
            "put_wall_concentration": put_wall.get("concentration"),
            "put_wall_strength": put_wall.get("strength"),
            "put_wall_distance_pct": round(put_distance_pct, 2) if put_distance_pct is not None else None,
            "oi_wall_signal": wall_signal,
        }

    def _option_oi_wall_levels(
        self,
        contracts: list[dict],
        underlying_price: float,
        atm_strike: float,
        side: str,
        limit: int = 5,
    ) -> list[dict]:
        """Rank nearby OI walls and add gamma-weighted magnet strength."""
        side_name = str(side or "").strip().upper()
        if underlying_price <= 0 or side_name not in {"CALL", "PUT"}:
            return []

        boundary = atm_strike or underlying_price
        eligible = [
            item for item in contracts
            if self._safe_float(item.get("open_interest"), 0.0) > 0
            and (
                self._safe_float(item.get("strike_price"), 0.0) >= boundary
                if side_name == "CALL"
                else self._safe_float(item.get("strike_price"), 0.0) <= boundary
            )
        ]
        if not eligible:
            return []

        oi_values = sorted(self._safe_float(item.get("open_interest"), 0.0) for item in eligible)
        midpoint = len(oi_values) // 2
        median_oi = (
            oi_values[midpoint]
            if len(oi_values) % 2
            else (oi_values[midpoint - 1] + oi_values[midpoint]) / 2.0
        )
        ranked = sorted(
            eligible,
            key=lambda item: (
                self._safe_float(item.get("open_interest"), 0.0),
                self._safe_float(item.get("total_volume"), 0.0),
                -abs(self._safe_float(item.get("strike_price"), 0.0) - underlying_price),
            ),
            reverse=True,
        )[: max(int(limit), 1)]

        levels: list[dict] = []
        for contract in ranked:
            strike = self._safe_float(contract.get("strike_price"), 0.0)
            open_interest = self._safe_float(contract.get("open_interest"), 0.0)
            volume = self._safe_float(contract.get("total_volume"), 0.0)
            greeks = contract.get("greeks") if isinstance(contract.get("greeks"), dict) else {}
            delta = abs(self._safe_float(greeks.get("delta", contract.get("delta")), 0.0))
            gamma = abs(self._safe_float(greeks.get("gamma", contract.get("gamma")), 0.0))
            concentration = open_interest / max(median_oi, 1.0)
            distance_pct = abs(strike - underlying_price) / underlying_price * 100.0
            distance_decay = 1.0 / (1.0 + (distance_pct / 5.0))
            volume_confirmation = 1.0 + min(volume / max(open_interest, 1.0), 1.0) * 0.25
            magnet_score = min(concentration * gamma * 10.0 * distance_decay * volume_confirmation, 1.0)
            magnet_strength = "STRONG" if magnet_score >= 0.65 else "MODERATE" if magnet_score >= 0.30 else "WEAK"
            levels.append(
                {
                    "strike": round(strike, 4),
                    "open_interest": round(open_interest),
                    "volume": round(volume),
                    "delta_abs": round(delta, 4),
                    "gamma": round(gamma, 6),
                    "concentration": round(concentration, 2),
                    "distance_pct": round(distance_pct, 2),
                    "magnet_score": round(magnet_score, 4),
                    "magnet_strength": magnet_strength,
                    "huge_oi_weak_magnet": concentration >= 2.3 and magnet_score < 0.30,
                }
            )
        return levels

    def _oi_finder_side_rows(
        self,
        chain_payload: dict,
        side: str,
        max_days_to_expiration: int = OI_FINDER_MAX_DAYS_TO_EXPIRATION,
        min_delta: float = 0.20,
    ) -> list[dict]:
        """Build TOS-style ATM-versus-OTM comparison rows for one option side."""
        side_name = str(side or "").strip().upper()
        if side_name not in {"CALL", "PUT"}:
            return []
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        if underlying_price <= 0:
            return []

        contracts = self._option_chain_contracts(chain_payload, side_name)
        expiries = sorted(
            {
                str(item.get("expiry_date") or "")
                for item in contracts
                if str(item.get("expiry_date") or "")
                and 0 <= int(item.get("daysToExpiration") or 0) <= max_days_to_expiration
            }
        )
        delta_targets = (0.50, 0.40, 0.35, 0.20)
        rows: list[dict] = []
        for expiry in expiries:
            expiry_contracts = [item for item in contracts if str(item.get("expiry_date") or "") == expiry]
            if not expiry_contracts:
                continue
            atm_contract = self._oi_finder_atm_contract(expiry_contracts, underlying_price)
            if not atm_contract:
                continue
            atm_strike = self._safe_float(atm_contract.get("strike_price"), 0.0)
            atm_volume = self._safe_float(atm_contract.get("total_volume"), 0.0)
            atm_open_interest = self._safe_float(atm_contract.get("open_interest"), 0.0)
            expected_move = self._expected_move_for_expiry(chain_payload, expiry)
            days_to_expiration = int(atm_contract.get("daysToExpiration") or 0)
            candidates = [
                item for item in expiry_contracts
                if (
                    min_delta <= abs(self._safe_float(item.get("delta"), 0.0)) <= 0.55
                    # Schwab reports +/-999 (or 0) greeks outside regular
                    # hours. A missing delta must not erase the OI wall map
                    # overnight, so unusable deltas pass through and the
                    # liquidity scoring below picks the representative rows.
                    or not (0.0 < abs(self._safe_float(item.get("delta"), 0.0)) <= 1.0)
                )
                and (
                    self._safe_float(item.get("strike_price"), 0.0) > atm_strike
                    if side_name == "CALL"
                    else self._safe_float(item.get("strike_price"), 0.0) < atm_strike
                )
            ]
            selected: list[dict] = []
            used_contracts: set[str] = set()
            for target_delta in delta_targets:
                available = [
                    item for item in candidates
                    if str(item.get("symbol") or item.get("description") or item.get("strike_price")) not in used_contracts
                ]
                if not available:
                    break
                contract = min(
                    available,
                    key=lambda item: (
                        abs(abs(self._safe_float(item.get("delta"), 0.0)) - target_delta),
                        -self._option_combined_liquidity_score(item),
                    ),
                )
                contract_key = str(contract.get("symbol") or contract.get("description") or contract.get("strike_price"))
                used_contracts.add(contract_key)
                selected.append(contract)

            # Keep the representative delta contracts, but also retain the
            # single biggest OI + volume wall for the expiry.  A provider can
            # report a slightly different delta from TOS (for example HOOD
            # 120C), and this ensures the actual liquidity wall is never
            # hidden solely because it misses a target-delta bucket.
            liquidity_wall_key = ""
            if candidates:
                liquidity_wall = max(candidates, key=self._option_combined_liquidity_score)
                liquidity_wall_key = str(
                    liquidity_wall.get("symbol")
                    or liquidity_wall.get("description")
                    or liquidity_wall.get("strike_price")
                )
                if liquidity_wall_key not in used_contracts:
                    used_contracts.add(liquidity_wall_key)
                    selected.append(liquidity_wall)

            # Preserve every relevant next support/resistance level.  These
            # contracts have more live volume or more open interest than the
            # same expiry's ATM contract, even when their delta does not line
            # up with a representative target bucket.
            high_atm_candidates = sorted(
                (
                    item for item in candidates
                    if self._safe_float(item.get("total_volume"), 0.0) > atm_volume
                    or self._safe_float(item.get("open_interest"), 0.0) > atm_open_interest
                ),
                key=self._option_combined_liquidity_score,
                reverse=True,
            )
            for contract in high_atm_candidates:
                contract_key = str(contract.get("symbol") or contract.get("description") or contract.get("strike_price"))
                if contract_key not in used_contracts:
                    used_contracts.add(contract_key)
                    selected.append(contract)

            for contract in selected:
                strike = self._safe_float(contract.get("strike_price"), 0.0)
                delta = abs(self._safe_float(contract.get("delta"), 0.0))
                volume = self._safe_float(contract.get("total_volume"), 0.0)
                open_interest = self._safe_float(contract.get("open_interest"), 0.0)
                contract_key = str(contract.get("symbol") or contract.get("description") or contract.get("strike_price"))
                above_atm_volume = volume > atm_volume
                above_atm_open_interest = open_interest > atm_open_interest
                liquidity_labels: list[str] = []
                if contract_key == liquidity_wall_key:
                    liquidity_labels.append("OI WALL")
                if above_atm_volume:
                    liquidity_labels.append("HIGH VOL")
                if above_atm_open_interest:
                    liquidity_labels.append("HIGH OI")
                otm_score = volume + open_interest
                atm_score = atm_volume + atm_open_interest
                liquidity_winner = "ATM > OTM" if atm_score >= otm_score else "OTM > ATM"
                flow_type = "Big real-time volume only" if volume > open_interest else "Big OI only"
                setup_type = (
                    "ATM momentum" if flow_type == "Big real-time volume only" and liquidity_winner == "ATM > OTM"
                    else "OTM momentum" if flow_type == "Big real-time volume only"
                    else "ATM positioning" if liquidity_winner == "ATM > OTM"
                    else "OTM positioning"
                )
                rows.append(
                    {
                        **self._option_contract_market_fields(contract),
                        "side": side_name,
                        "expiry": expiry,
                        "expected_move": expected_move,
                        "days_to_expiration": days_to_expiration,
                        "delta": round(delta, 4),
                        "strike": strike,
                        "volume": round(volume),
                        "open_interest": round(open_interest),
                        "volume_oi_ratio": round(volume / max(open_interest, 1.0), 2),
                        "atm_strike": atm_strike,
                        "atm_volume": round(atm_volume),
                        "atm_open_interest": round(atm_open_interest),
                        "flow_type": flow_type,
                        "liquidity_winner": liquidity_winner,
                        "setup_type": setup_type,
                        "scanner_tag": f"{flow_type} + {liquidity_winner}",
                        "is_liquidity_wall": contract_key == liquidity_wall_key,
                        "above_atm_volume": above_atm_volume,
                        "above_atm_open_interest": above_atm_open_interest,
                        "liquidity_labels": liquidity_labels,
                        "liquidity_score": round(otm_score),
                        "atm_liquidity_score": round(atm_score),
                    }
                )

        # The same OTM strike can appear in several expiration cycles.  Show a
        # single wall for that strike: the expiry with the greatest combined
        # volume + OI wins, with the nearer expiry breaking a tie.  This keeps
        # the Finder focused on the leading wall instead of repeating a price
        # for this week, next week, and later expiries.
        leading_by_strike: dict[float, dict] = {}
        for row in rows:
            strike = self._safe_float(row.get("strike"), 0.0)
            current = leading_by_strike.get(strike)
            if current is None or (
                self._safe_float(row.get("liquidity_score"), 0.0),
                -int(row.get("days_to_expiration") or 0),
                self._safe_float(row.get("volume"), 0.0),
            ) > (
                self._safe_float(current.get("liquidity_score"), 0.0),
                -int(current.get("days_to_expiration") or 0),
                self._safe_float(current.get("volume"), 0.0),
            ):
                leading_by_strike[strike] = row

        rows = list(leading_by_strike.values())
        max_liquidity = max((self._safe_float(row.get("liquidity_score"), 0.0) for row in rows), default=1.0)
        for row in rows:
            otm_score = self._safe_float(row.get("liquidity_score"), 0.0)
            atm_score = self._safe_float(row.get("atm_liquidity_score"), 0.0)
            volume = self._safe_float(row.get("volume"), 0.0)
            open_interest = self._safe_float(row.get("open_interest"), 0.0)
            relative_liquidity = otm_score / max(max_liquidity, 1.0)
            atm_dominance = min(otm_score / max(atm_score, 1.0) / 2.0, 1.0)
            volume_confirmation = min(volume / max(open_interest, 1.0), 1.0)
            strength_score = round((relative_liquidity * 55.0) + (atm_dominance * 30.0) + (volume_confirmation * 15.0))
            row["strength_score"] = strength_score
            row["strength"] = "STRONG" if strength_score >= 70 else "MODERATE" if strength_score >= 40 else "WEAK"
        return sorted(rows, key=lambda row: (row["days_to_expiration"], -row["delta"], row["strike"]))

    def _oi_finder_selected_expiry_chain_rows(
        self,
        chain_payload: dict,
        max_days_to_expiration: int = OI_FINDER_MAX_DAYS_TO_EXPIRATION,
    ) -> list[dict]:
        """Return the full two-sided TOS-style chain for every live expiry.

        The Finder tables intentionally collapse OTM levels across expirations.
        This data set does the opposite: it preserves each expiry and includes
        its actual contracts so the Charts & OI panel can show Calls | Strike |
        Puts without applying the Finder's OTM delta-band filter.  The source
        request's strike count is the only display bound.
        """
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        if underlying_price <= 0:
            return []

        output: list[dict] = []
        for side_name in ("CALL", "PUT"):
            contracts = self._option_chain_contracts(chain_payload, side_name)
            by_expiry: dict[str, list[dict]] = {}
            for contract in contracts:
                expiry = str(contract.get("expiry_date") or "")
                try:
                    expiry_days = (datetime.fromisoformat(expiry[:10]).date() - datetime.now().date()).days
                except (TypeError, ValueError):
                    continue
                # A normalized expiry date keeps the DTE consistent even when
                # the provider's optional daysToExpiration field is stale.
                days_to_expiration = expiry_days
                if expiry and 0 <= days_to_expiration <= max_days_to_expiration:
                    by_expiry.setdefault(expiry, []).append(contract)

            for expiry, expiry_contracts in by_expiry.items():
                atm_contract = self._oi_finder_atm_contract(expiry_contracts, underlying_price)
                if not atm_contract:
                    continue
                atm_strike = self._safe_float(atm_contract.get("strike_price"), 0.0)
                expected_move = self._expected_move_for_expiry(chain_payload, expiry)
                for contract in expiry_contracts:
                    strike = self._safe_float(contract.get("strike_price"), 0.0)
                    delta = abs(self._safe_float(contract.get("delta"), 0.0))
                    is_atm = abs(strike - atm_strike) < 0.0001
                    output.append(
                        {
                            **self._option_contract_market_fields(contract),
                            "side": side_name,
                            "expiry": expiry,
                            "days_to_expiration": (datetime.fromisoformat(expiry[:10]).date() - datetime.now().date()).days,
                            # Schwab returns no straddle marks outside regular
                            # hours, so an expiry's expected move can be None.
                            "expected_move": round(expected_move, 4) if expected_move is not None else None,
                            "strike": strike,
                            "delta": round(delta, 4),
                            "volume": round(self._safe_float(contract.get("total_volume"), 0.0)),
                            "open_interest": round(self._safe_float(contract.get("open_interest"), 0.0)),
                            "is_atm": is_atm,
                        }
                    )
        # Preserve every returned contract, but tag the leading OI and volume
        # levels within each exact expiry/side.  These are visual cues for the
        # TOS-style chain, not a filter and not a trade recommendation.
        grouped_rows: dict[tuple[str, str], list[dict]] = {}
        for row in output:
            grouped_rows.setdefault((str(row["expiry"]), str(row["side"])), []).append(row)
        for rows in grouped_rows.values():
            positive_oi = [row for row in rows if self._safe_float(row.get("open_interest"), 0.0) > 0]
            positive_volume = [row for row in rows if self._safe_float(row.get("volume"), 0.0) > 0]
            # Highlight the five largest OI levels for each exact expiry and
            # side.  Limiting this to three hid meaningful clusters such as a
            # low-delta contract that still carried one of the expiry's largest
            # reported OI readings.
            top_oi = {
                float(row["strike"])
                for row in sorted(positive_oi, key=lambda item: self._safe_float(item.get("open_interest"), 0.0), reverse=True)[:5]
            }
            # Do not let strong OTM levels hide a meaningful deep-ITM OI level.
            # The chain remains fully unfiltered (all deltas), while these extra
            # tags make the strongest ITM contracts visible like TOS shading.
            side_name = str(rows[0].get("side") or "")
            itm_oi = [
                row for row in positive_oi
                if (side_name == "CALL" and self._safe_float(row.get("strike"), 0.0) < underlying_price)
                or (side_name == "PUT" and self._safe_float(row.get("strike"), 0.0) > underlying_price)
            ]
            top_oi.update(
                float(row["strike"])
                for row in sorted(itm_oi, key=lambda item: self._safe_float(item.get("open_interest"), 0.0), reverse=True)[:3]
            )
            top_volume = {
                float(row["strike"])
                for row in sorted(positive_volume, key=lambda item: self._safe_float(item.get("volume"), 0.0), reverse=True)[:3]
            }
            for row in rows:
                row["is_high_open_interest"] = float(row["strike"]) in top_oi
                row["is_high_volume"] = float(row["strike"]) in top_volume

        return sorted(
            output,
            key=lambda row: (int(row["days_to_expiration"]), str(row["expiry"]), -float(row["strike"]), row["side"]),
        )

    def _oi_finder_tos_script_levels(
        self,
        chain_payload: dict,
        max_days_to_expiration: int = OI_FINDER_MAX_DAYS_TO_EXPIRATION,
        min_delta: float = 0.0,
        max_delta: float = 1.0,
        levels_per_side: int = 5,
    ) -> list[dict]:
        """Return raw, expiry-specific OI and cumulative-volume levels for a static ThinkScript export.

        This deliberately does not collapse a strike across expirations.  The Finder table does
        collapse duplicate strikes to keep the UI concise, while a TOS script must use the exact
        current chain for the expiry the user selected.
        """
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        if underlying_price <= 0:
            return []
        contracts_by_side = {
            "CALL": self._option_chain_contracts(chain_payload, "CALL"),
            "PUT": self._option_chain_contracts(chain_payload, "PUT"),
        }
        expiry_days: dict[str, int] = {}
        for contracts in contracts_by_side.values():
            for contract in contracts:
                expiry = str(contract.get("expiry_date") or "")
                days = int(contract.get("daysToExpiration") or 0)
                if expiry and 0 <= days <= max_days_to_expiration:
                    expiry_days[expiry] = min(expiry_days.get(expiry, days), days)

        result: list[dict] = []
        for expiry in sorted(expiry_days, key=lambda item: (expiry_days[item], item)):
            level_set = {
                "expiry": expiry,
                "daysToExpiration": expiry_days[expiry],
                "callLevels": [],
                "putLevels": [],
            }
            for side_name, key in (("CALL", "callLevels"), ("PUT", "putLevels")):
                expiry_contracts = [
                    item for item in contracts_by_side[side_name]
                    if str(item.get("expiry_date") or "") == expiry
                ]
                atm_contract = self._oi_finder_atm_contract(expiry_contracts, underlying_price)
                if not atm_contract:
                    continue
                atm_strike = self._safe_float(atm_contract.get("strike_price"), 0.0)
                levels_by_strike: dict[float, dict] = {}
                for contract in expiry_contracts:
                    strike = self._safe_float(contract.get("strike_price"), 0.0)
                    delta = abs(self._safe_float(contract.get("delta"), 0.0))
                    # Chart levels are the *highest reported OI* contracts for
                    # each exact expiry and side. Do not apply the Finder's
                    # OTM/delta scan filter here: it can remove every put (or
                    # call) on a thin chain and leave the chart with no levels.
                    # The TOS-style option chain remains the place to inspect
                    # moneyness; the chart must show the actual largest levels.
                    open_interest = self._safe_float(contract.get("open_interest"), 0.0)
                    if open_interest <= 0:
                        continue
                    candidate = {
                        "strike": round(strike, 4),
                        "openInterest": round(open_interest),
                        "volume": round(self._safe_float(contract.get("total_volume"), 0.0)),
                        "delta": round(delta, 4),
                    }
                    existing = levels_by_strike.get(strike)
                    if existing is None or (candidate["openInterest"], candidate["volume"]) > (existing["openInterest"], existing["volume"]):
                        levels_by_strike[strike] = candidate
                level_set[key] = sorted(
                    levels_by_strike.values(),
                    key=lambda item: (-item["openInterest"], -item["volume"], item["strike"] if side_name == "CALL" else -item["strike"]),
                )[:levels_per_side]
            result.append(level_set)
        return result

    def _oi_finder_current_atm(self, chain_payload: dict, max_days_to_expiration: int = OI_FINDER_MAX_DAYS_TO_EXPIRATION) -> dict:
        """Return the nearest expiry's call and put ATM metrics for the Finder header."""
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        if underlying_price <= 0:
            return {}

        calls = self._option_chain_contracts(chain_payload, "CALL")
        puts = self._option_chain_contracts(chain_payload, "PUT")
        expiry_candidates: list[tuple[int, str]] = []
        for contract in calls + puts:
            expiry = str(contract.get("expiry_date") or "")
            days_to_expiration = int(self._safe_float(contract.get("daysToExpiration"), -1))
            if expiry and 0 <= days_to_expiration <= max_days_to_expiration:
                expiry_candidates.append((days_to_expiration, expiry))
        if not expiry_candidates:
            return {}

        days_to_expiration, expiry = min(expiry_candidates, key=lambda item: (item[0], item[1]))

        def atm_metrics(contracts: list[dict]) -> dict:
            eligible = [item for item in contracts if str(item.get("expiry_date") or "") == expiry]
            if not eligible:
                return {}
            contract = self._oi_finder_atm_contract(eligible, underlying_price)
            if not contract:
                return {}
            return {
                "strike": self._safe_float(contract.get("strike_price"), 0.0) or None,
                "volume": round(self._safe_float(contract.get("total_volume"), 0.0)),
                "openInterest": round(self._safe_float(contract.get("open_interest"), 0.0)),
            }

        return {
            "expiry": expiry,
            "daysToExpiration": days_to_expiration,
            "expectedMove": self._expected_move_for_expiry(chain_payload, expiry),
            "call": atm_metrics(calls),
            "put": atm_metrics(puts),
        }

    def _oi_finder_volume_key(self, side: str, expiry: str, strike: object) -> str:
        """Return a stable contract identity for Finder volume snapshots."""
        normalized_side = str(side or "").strip().upper()
        normalized_expiry = str(expiry or "").strip()
        normalized_strike = self._safe_float(strike, 0.0)
        return f"{normalized_side}|{normalized_expiry}|{normalized_strike:.4f}"

    def _oi_finder_volume_snapshot(
        self,
        current_atm: dict,
        call_rows: list[dict],
        put_rows: list[dict],
        recorded_at: datetime,
        chain_payload: dict | None = None,
    ) -> dict:
        """Capture all in-range 0-14 DTE contract volumes for live comparison."""
        contracts: dict[str, dict] = {}

        def add_contract(side: str, role: str, expiry: object, strike: object, volume: object) -> None:
            if not expiry or self._safe_float(strike, 0.0) <= 0:
                return
            key = self._oi_finder_volume_key(side, str(expiry), strike)
            contracts[key] = {
                "side": str(side).strip().upper(),
                "role": role,
                "expiry": str(expiry),
                "strike": self._safe_float(strike, 0.0),
                "volume": round(self._safe_float(volume, 0.0)),
            }

        # Preserve every ATM / OTM contract that the heatmap can show.  This
        # lets the call-versus-put live-volume leader use the complete visible
        # 0.20-0.55 delta band instead of only the small ranked wall table.
        if isinstance(chain_payload, dict):
            for side in ("CALL", "PUT"):
                for contract in self._option_chain_contracts(chain_payload, side):
                    days_to_expiration = int(self._safe_float(contract.get("daysToExpiration"), -1))
                    delta = abs(self._safe_float(contract.get("delta"), 0.0))
                    if not 0 <= days_to_expiration <= OI_FINDER_MAX_DAYS_TO_EXPIRATION or not 0.20 <= delta <= 0.55:
                        continue
                    add_contract(
                        side,
                        "CHAIN",
                        contract.get("expiry_date"),
                        contract.get("strike_price"),
                        contract.get("total_volume"),
                    )

        expiry = current_atm.get("expiry")
        for side in ("call", "put"):
            metrics = current_atm.get(side) if isinstance(current_atm.get(side), dict) else {}
            add_contract(side, "ATM", expiry, metrics.get("strike"), metrics.get("volume"))
        for side, rows in (("call", call_rows), ("put", put_rows)):
            for row in rows:
                add_contract(side, "OTM", row.get("expiry"), row.get("strike"), row.get("volume"))

        aggregates = {
            "callAtm": 0,
            "callOtm": 0,
            "putAtm": 0,
            "putOtm": 0,
        }
        for contract in contracts.values():
            side_prefix = "call" if contract.get("side") == "CALL" else "put"
            role_suffix = "Atm" if contract.get("role") == "ATM" else "Otm"
            aggregates[f"{side_prefix}{role_suffix}"] += self._safe_float(contract.get("volume"), 0.0)

        # When 0DTE has already expired or no longer has the displayed
        # 0.20-0.55 delta candidates, move the rate card to the next active
        # expiry.  It keeps the live card useful after the close while still
        # preferring 0DTE whenever both call and put OTM candidates exist.
        front_expiry = str(current_atm.get("expiry") or "")
        if isinstance(chain_payload, dict):
            spot = self._option_underlying_price_from_chain(chain_payload)
            eligible_expiries: dict[str, dict] = {}
            for side in ("CALL", "PUT"):
                for contract in self._option_chain_contracts(chain_payload, side):
                    expiry = str(contract.get("expiry_date") or "")
                    dte = int(self._safe_float(contract.get("daysToExpiration"), -1))
                    strike = self._safe_float(contract.get("strike_price"), 0.0)
                    delta = abs(self._safe_float(contract.get("delta"), 0.0))
                    if not expiry or not 0 <= dte <= OI_FINDER_MAX_DAYS_TO_EXPIRATION or not 0.20 <= delta <= 0.55 or strike <= 0:
                        continue
                    # Accept the nearest ATM strike even when the spot falls
                    # between strikes, then retain only the OTM direction.
                    tolerance = max(0.5, spot * 0.002)
                    if spot > 0 and ((side == "CALL" and strike < spot - tolerance) or (side == "PUT" and strike > spot + tolerance)):
                        continue
                    entry = eligible_expiries.setdefault(expiry, {"dte": dte, "CALL": set(), "PUT": set()})
                    entry[side].add(strike)
            active = [
                (int(entry["dte"]), expiry)
                for expiry, entry in eligible_expiries.items()
                if entry["CALL"] and entry["PUT"]
            ]
            if active:
                front_expiry = min(active, key=lambda item: (item[0], item[1]))[1]

        return {
            "recordedAt": recorded_at,
            "underlyingPrice": round(self._option_underlying_price_from_chain(chain_payload or {}), 4),
            "frontExpiry": front_expiry,
            "contracts": contracts,
            "aggregates": {key: round(value) for key, value in aggregates.items()},
        }

    def _oi_finder_intraday_volume_timeline(self, symbol: str, snapshot: dict) -> dict:
        """Store minute-level cumulative volume for the Finder's front expiry.

        Schwab/TOS reports cumulative session volume.  The UI intentionally
        receives those raw cumulative readings and calculates delta volume
        (including the counter-reset rule) per selected strike.
        """
        recorded_at = snapshot.get("recordedAt")
        if not isinstance(recorded_at, datetime):
            recorded_at = datetime.now().astimezone()
        normalized_symbol = str(symbol or "").strip().upper()
        front_expiry = str(snapshot.get("frontExpiry") or "")
        spot = self._safe_float(snapshot.get("underlyingPrice"), 0.0)
        calls: dict[str, int] = {}
        puts: dict[str, int] = {}
        for contract in (snapshot.get("contracts") or {}).values():
            if str(contract.get("expiry") or "") != front_expiry:
                continue
            strike = self._safe_float(contract.get("strike"), 0.0)
            # Keep the same practical range as the Finder: the current ATM
            # plus its 0.20-0.55 delta OTM candidates.  It prevents far-away
            # low-delta contracts from cluttering the live panels.
            if strike <= 0:
                continue
            tolerance = max(0.5, spot * 0.002)
            side = str(contract.get("side") or "").upper()
            if spot > 0 and ((side == "CALL" and strike < spot - tolerance) or (side == "PUT" and strike > spot + tolerance)):
                continue
            strike_key = f"{strike:.4f}".rstrip("0").rstrip(".")
            target = calls if side == "CALL" else puts
            target[strike_key] = max(target.get(strike_key, 0), round(self._safe_float(contract.get("volume"), 0.0)))

        minute_start = recorded_at.replace(second=0, microsecond=0)
        current = {
            "time": int(minute_start.timestamp()),
            "recordedAt": _serialize_value(recorded_at),
            "frontExpiry": front_expiry,
            "spot": round(spot, 4) if spot > 0 else None,
            "calls": calls,
            "puts": puts,
        }
        # Save a compact cumulative reading, not every full option chain. This
        # lets the MAG7 worker continue the exact same rate-of-change bars
        # after the backend restarts. Manual 400-watchlist searches save only
        # the ticker the trader actually opened.
        if normalized_symbol and front_expiry:
            self.repository.upsert_option_chain_intraday_volume_bucket({
                "symbol": normalized_symbol,
                "front_expiry": front_expiry,
                "bucket_time": current["time"],
                "recorded_at": current["recordedAt"],
                "spot": spot,
                "calls": calls,
                "puts": puts,
            })
        # One regular-session day plus a little extended-hours room is ample
        # for an active Finder tab and prevents this in-memory helper from
        # growing as the general watchlist changes.
        retention = minute_start - timedelta(hours=18)
        restored = self.repository.option_chain_intraday_volume_buckets(
            normalized_symbol,
            front_expiry,
            hours=18,
        )
        with self.oi_finder_lock:
            history = self.oi_finder_intraday_volume_history.setdefault(normalized_symbol, [])
            # Merge the saved minute buckets with this process's short trail.
            # The current reading wins when both are in the same minute.
            by_time = {
                int(item.get("time") or 0): item
                for item in [*restored, *history]
                if int(item.get("time") or 0) > 0 and item.get("frontExpiry") == front_expiry
            }
            by_time[current["time"]] = current
            history[:] = [by_time[key] for key in sorted(by_time)]
            history[:] = [
                item for item in history
                if int(item.get("time") or 0) >= int(retention.timestamp())
            ]
            # A new expiry should not be plotted against a prior front expiry.
            visible = [item for item in history if item.get("frontExpiry") == front_expiry]

        available_strikes = sorted(
            {
                *[self._safe_float(strike, 0.0) for item in visible for strike in (item.get("calls") or {})],
                *[self._safe_float(strike, 0.0) for item in visible for strike in (item.get("puts") or {})],
            }
            - {0.0}
        )
        return {
            "bucketSeconds": 60,
            "frontExpiry": front_expiry,
            "spot": round(spot, 4) if spot > 0 else None,
            "startedAt": visible[0].get("recordedAt") if visible else _serialize_value(recorded_at),
            "updatedAt": _serialize_value(recorded_at),
            "availableStrikes": [round(strike, 4) for strike in available_strikes],
            "buckets": visible,
        }

    def _oi_finder_volume_momentum(self, symbol: str, snapshot: dict) -> dict:
        """Record a snapshot and calculate rate-of-change for 1/5/15 minutes."""
        recorded_at = snapshot.get("recordedAt")
        if not isinstance(recorded_at, datetime):
            recorded_at = datetime.now().astimezone()
            snapshot["recordedAt"] = recorded_at
        normalized_symbol = str(symbol or "").strip().upper()
        window_seconds = (60, 300, 900)
        retention = recorded_at - timedelta(minutes=20)

        with self.oi_finder_lock:
            history = self.oi_finder_volume_history.setdefault(normalized_symbol, [])
            history.append(snapshot)
            history[:] = [item for item in history if isinstance(item.get("recordedAt"), datetime) and item["recordedAt"] >= retention]

            current_contracts = snapshot.get("contracts") if isinstance(snapshot.get("contracts"), dict) else {}
            momentum_contracts: dict[str, dict] = {}
            prior_history = history[:-1]
            for contract_key, current in current_contracts.items():
                current_volume = self._safe_float(current.get("volume"), 0.0)
                windows: dict[str, dict] = {}
                for seconds in window_seconds:
                    candidates = [
                        item for item in prior_history
                        if contract_key in (item.get("contracts") or {})
                        and (recorded_at - item["recordedAt"]).total_seconds() >= seconds * 0.75
                    ]
                    if not candidates:
                        continue
                    prior = min(
                        candidates,
                        key=lambda item: abs((recorded_at - item["recordedAt"]).total_seconds() - seconds),
                    )
                    elapsed_seconds = max((recorded_at - prior["recordedAt"]).total_seconds(), 1.0)
                    prior_volume = self._safe_float(prior["contracts"][contract_key].get("volume"), 0.0)
                    volume_change = max(current_volume - prior_volume, 0.0)
                    windows[str(seconds)] = {
                        "volumeChange": round(volume_change),
                        "ratePerMinute": round(volume_change / elapsed_seconds * 60.0, 2),
                        "elapsedSeconds": round(elapsed_seconds),
                    }
                momentum_contracts[contract_key] = {
                    **current,
                    "windows": windows,
                }

            started_at = history[0]["recordedAt"] if history else recorded_at
            series: list[dict] = []
            previous_snapshot: dict | None = None
            for item in history:
                aggregates = item.get("aggregates") if isinstance(item.get("aggregates"), dict) else {}
                point = {"time": int(item["recordedAt"].timestamp())}
                if previous_snapshot is not None:
                    previous_aggregates = previous_snapshot.get("aggregates") if isinstance(previous_snapshot.get("aggregates"), dict) else {}
                    elapsed_seconds = max((item["recordedAt"] - previous_snapshot["recordedAt"]).total_seconds(), 1.0)
                    for aggregate_key in ("callAtm", "callOtm", "putAtm", "putOtm"):
                        current_value = self._safe_float(aggregates.get(aggregate_key), 0.0)
                        prior_value = self._safe_float(previous_aggregates.get(aggregate_key), 0.0)
                        point[f"{aggregate_key}Rate"] = round(max(current_value - prior_value, 0.0) / elapsed_seconds * 60.0, 2)
                series.append(point)
                previous_snapshot = item
            return {
                "updatedAt": _serialize_value(recorded_at),
                "startedAt": _serialize_value(started_at),
                "refreshSeconds": 15,
                "windows": list(window_seconds),
                "contracts": momentum_contracts,
                "series": series,
                "intradayTimeline": self._oi_finder_intraday_volume_timeline(normalized_symbol, snapshot),
            }

    def _attach_oi_finder_volume_momentum(
        self,
        current_atm: dict,
        call_rows: list[dict],
        put_rows: list[dict],
        momentum: dict,
    ) -> None:
        """Attach each contract's live volume ROC to the Finder response rows."""
        contracts = momentum.get("contracts") if isinstance(momentum.get("contracts"), dict) else {}
        expiry = current_atm.get("expiry")
        for side in ("call", "put"):
            metrics = current_atm.get(side) if isinstance(current_atm.get(side), dict) else None
            if not metrics:
                continue
            key = self._oi_finder_volume_key(side, expiry, metrics.get("strike"))
            metrics["volumeMomentum"] = contracts.get(key, {"windows": {}, "volume": metrics.get("volume")})
        for side, rows in (("call", call_rows), ("put", put_rows)):
            for row in rows:
                key = self._oi_finder_volume_key(side, row.get("expiry"), row.get("strike"))
                row["volumeMomentum"] = contracts.get(key, {"windows": {}, "volume": row.get("volume")})

    def _record_oi_finder_daily_chain_snapshot(
        self,
        symbol: str,
        chain_payload: dict,
        current_atm: dict,
        captured_at: datetime,
        include_history: bool = True,
    ) -> dict:
        """Store the latest daily values across the Finder's 0–14 DTE chain."""
        eastern_time = captured_at.astimezone(ZoneInfo(EASTERN_TZ))
        # Weekend reads repeat Friday's cumulative session values. Do not turn
        # that repeat into a false daily history point for the heatmap.  The
        # current chain is still useful for the screen, so return it as a
        # clearly marked live-only heatmap instead of an empty panel.
        if eastern_time.weekday() >= 5:
            if not include_history:
                return {}
            return self._oi_finder_live_chain_heatmap(symbol, chain_payload, eastern_time)
        snapshot_date = eastern_time.date().isoformat()
        snapshots: list[dict] = []
        for side in ("CALL", "PUT"):
            for contract in self._option_chain_contracts(chain_payload, side):
                days_to_expiration = int(self._safe_float(contract.get("daysToExpiration"), -1))
                if not 0 <= days_to_expiration <= OI_FINDER_MAX_DAYS_TO_EXPIRATION:
                    continue
                expiry = str(contract.get("expiry_date") or "").strip()
                if not expiry:
                    continue
                strike = self._safe_float(contract.get("strike_price"), 0.0)
                if strike <= 0:
                    continue
                snapshots.append(
                    {
                        "snapshot_date": snapshot_date,
                        "captured_at": eastern_time.isoformat(),
                        "symbol": symbol,
                        "expiry": expiry,
                        "side": side,
                        "strike": strike,
                        "volume": self._safe_float(contract.get("total_volume"), 0.0),
                        "open_interest": self._safe_float(contract.get("open_interest"), 0.0),
                        "gamma": self._safe_float(contract.get("gamma"), 0.0),
                        "delta": self._safe_float(contract.get("delta"), 0.0),
                    }
                )
        self.repository.upsert_option_chain_daily_snapshots(snapshots)
        if not include_history:
            return {}
        return self._oi_finder_daily_liquidity_heatmap(symbol)

    def _oi_finder_live_chain_heatmap(
        self,
        symbol: str,
        chain_payload: dict,
        captured_at: datetime,
    ) -> dict:
        """Return the current 0-14 DTE chain without writing a daily snapshot.

        This is used outside trading days (or before historical snapshots are
        available) so the Finder can show the exact live chain rather than an
        empty chart.  ``liveOnly`` lets the frontend label it correctly and
        prevents it being treated as prior-day history.
        """
        snapshot_date = captured_at.date().isoformat()
        rows_by_side: dict[str, list[dict]] = {"CALL": [], "PUT": []}
        expiries: set[str] = set()
        for side in ("CALL", "PUT"):
            for contract in self._option_chain_contracts(chain_payload, side):
                dte = int(self._safe_float(contract.get("daysToExpiration"), -1))
                expiry = str(contract.get("expiry_date") or "").strip()
                strike = self._safe_float(contract.get("strike_price"), 0.0)
                if not (0 <= dte <= OI_FINDER_MAX_DAYS_TO_EXPIRATION) or not expiry or strike <= 0:
                    continue
                expiries.add(expiry)
                rows_by_side[side].append(
                    {
                        "date": snapshot_date,
                        "capturedAt": captured_at.isoformat(),
                        "expiry": expiry,
                        "strike": round(strike, 4),
                        "volume": round(self._safe_float(contract.get("total_volume"), 0.0)),
                        "openInterest": round(self._safe_float(contract.get("open_interest"), 0.0)),
                        "delta": self._safe_float(contract.get("delta"), 0.0),
                    }
                )
        return {
            "symbol": symbol.upper(),
            "expiry": None,
            "scope": "Current live 0-31 DTE option chain (not stored as daily history)",
            "days": [snapshot_date] if any(rows_by_side.values()) else [],
            "expiries": sorted(expiries),
            "retentionDays": 183,
            "liveOnly": True,
            "call": rows_by_side["CALL"],
            "put": rows_by_side["PUT"],
        }

    def _oi_finder_unusual_otm_activity(
        self,
        symbol: str,
        chain_payload: dict,
        current_atm: dict,
    ) -> dict:
        """Score OTM call/put activity from live Schwab fields and saved daily snapshots.

        Schwab supplies the current chain's ``totalVolume`` and reported
        ``openInterest``.  Yesterday and the five-day average are only used
        when this application has saved the same exact contract previously.
        A missing snapshot is kept as ``None`` rather than estimated.
        """
        price = self._option_underlying_price_from_chain(chain_payload)
        expiry = str(current_atm.get("expiry") or "")
        if price <= 0 or not expiry:
            return {
                "available": False,
                "message": "Waiting for a live underlying price and selected expiry.",
                "signals": [],
                "strongestBullish": None,
                "strongestBearish": None,
            }
        classified = classify_strikes(price, chain_payload, expiry)
        atm_strike = classified.get("atmStrike")
        history = self.repository.option_chain_daily_snapshots(symbol, expiry=expiry, days=183)

        def exact_history(contract: dict) -> list[dict]:
            side = str(contract.get("optionType") or "").upper()
            strike = round(self._safe_float(contract.get("strike"), 0.0), 4)
            return [
                row for row in history
                if str(row.get("side") or "").upper() == side
                and round(self._safe_float(row.get("strike"), 0.0), 4) == strike
            ]

        signals: list[dict] = []
        for side_key, atm_key in (("otmCalls", "atmCalls"), ("otmPuts", "atmPuts")):
            atm_rows = classified.get(atm_key) or []
            atm_data = atm_rows[0] if atm_rows else {}
            for contract in classified.get(side_key) or []:
                metrics = compute_signal_metrics(contract, atm_data, exact_history(contract))
                score = score_signal(metrics)
                signals.append({
                    "optionType": contract.get("optionType"),
                    "expiry": contract.get("expiry"),
                    "strike": contract.get("strike"),
                    "mark": contract.get("mark") or None,
                    "bid": contract.get("bid") or None,
                    "ask": contract.get("ask") or None,
                    "metrics": metrics,
                    **score,
                })

        strongest_bullish = find_strongest_bullish_signal(signals)
        strongest_bearish = find_strongest_bearish_signal(signals)
        history_ready = bool(signals) and all(signal.get("metrics", {}).get("history_days", 0) >= 5 for signal in signals)
        return {
            "available": bool(signals),
            "ticker": str(symbol or "").upper(),
            "currentStockPrice": round(price, 4),
            "expiry": expiry,
            "atmStrike": atm_strike,
            "historyReady": history_ready,
            "message": (
                "Scores use saved daily option-chain snapshots."
                if history_ready
                else "Collecting daily chain history for yesterday and the five-day average; current volume and OI are live."
            ),
            "signals": signals,
            "strongestBullish": strongest_bullish,
            "strongestBearish": strongest_bearish,
            "directionNote": "OTM calls are call-side activity and OTM puts are put-side activity; volume and OI alone do not establish trade direction.",
        }

    def _oi_finder_daily_liquidity_heatmap(self, symbol: str, expiry: str | None = None) -> dict:
        """Return daily option volume/OI readings and day-over-day changes for the Finder."""
        stored_rows = self.repository.option_chain_daily_snapshots(symbol, expiry, days=183)
        # Older database rows may include a weekend refresh. Schwab repeats the
        # prior trading session's cumulative option volume on weekends, so it
        # is not a new daily observation and must not appear as a chart bar.
        def is_trading_snapshot(row: dict) -> bool:
            try:
                return date.fromisoformat(str(row.get("snapshot_date") or "")).weekday() < 5
            except ValueError:
                return False
        stored_rows = [row for row in stored_rows if is_trading_snapshot(row)]
        rows_by_side = {"CALL": [], "PUT": []}
        days = sorted({str(row.get("snapshot_date") or "") for row in stored_rows if row.get("snapshot_date")})
        expiries = sorted({str(row.get("expiry") or "") for row in stored_rows if row.get("expiry")})
        previous_by_side_strike: dict[tuple[str, str, float], dict] = {}
        for row in stored_rows:
            side = str(row.get("side") or "").upper()
            if side not in rows_by_side:
                continue
            strike = self._safe_float(row.get("strike"), 0.0)
            volume = self._safe_float(row.get("volume"), 0.0)
            open_interest = self._safe_float(row.get("open_interest"), 0.0)
            gamma = self._safe_float(row.get("gamma"), 0.0)
            delta = self._safe_float(row.get("delta"), 0.0)
            contract_expiry = str(row.get("expiry") or "")
            key = (side, contract_expiry, strike)
            prior = previous_by_side_strike.get(key)
            rows_by_side[side].append(
                {
                    "date": str(row.get("snapshot_date") or ""),
                    "capturedAt": row.get("captured_at"),
                    "expiry": contract_expiry,
                    "strike": strike,
                    "volume": round(volume),
                    "openInterest": round(open_interest),
                    "gamma": gamma,
                    "delta": delta,
                    "gammaWeightedOpenInterest": round(abs(gamma * open_interest), 6),
                    "volumeChange": round(volume - self._safe_float(prior.get("volume"), 0.0)) if prior else None,
                    "openInterestChange": round(open_interest - self._safe_float(prior.get("open_interest"), 0.0)) if prior else None,
                }
            )
            previous_by_side_strike[key] = {"volume": volume, "open_interest": open_interest}
        return {
            "symbol": str(symbol or "").upper(),
            "expiry": expiry,
            "scope": "0–14 DTE option chain" if not expiry else "Selected expiry",
            "days": days,
            "expiries": expiries,
            "retentionDays": 183,
            "call": rows_by_side["CALL"],
            "put": rows_by_side["PUT"],
        }

    def _oi_finder_persistent_option_activity(self, symbol: str) -> dict:
        """Classify sustained unusual volume for each exact saved option contract.

        A contract is never combined across expiry, strike, or side.  The
        current four saved trading-day readings are compared with the five
        saved trading-day readings immediately before them.  OI is daily data,
        so a position-building classification requires a later daily OI
        confirmation rather than any intraday OI inference.
        """
        normalized_symbol = str(symbol or "").strip().upper()
        stored_rows = self.repository.option_chain_daily_snapshots(normalized_symbol, days=183)
        dates = sorted({str(row.get("snapshot_date") or "") for row in stored_rows if row.get("snapshot_date")})
        records_by_contract: dict[tuple[str, str, float], dict[str, dict]] = {}
        for row in stored_rows:
            side = str(row.get("side") or "").upper()
            expiry = str(row.get("expiry") or "")
            snapshot_date = str(row.get("snapshot_date") or "")
            strike = self._safe_float(row.get("strike"), 0.0)
            if side not in {"CALL", "PUT"} or not expiry or not snapshot_date or strike <= 0:
                continue
            contract_key = (side, expiry, round(strike, 4))
            contract_days = records_by_contract.setdefault(contract_key, {})
            current = contract_days.get(snapshot_date)
            if current is None or str(row.get("captured_at") or "") >= str(current.get("captured_at") or ""):
                contract_days[snapshot_date] = row

        required_days = 9
        activity_rows: list[dict] = []
        ready_contract_count = 0
        waiting_contract_count = 0
        no_baseline_count = 0
        for (side, expiry, strike), rows_by_date in records_by_contract.items():
            history = [rows_by_date[day] for day in sorted(rows_by_date)]
            if len(history) < required_days:
                waiting_contract_count += 1
                continue
            baseline_rows = history[-9:-4]
            review_rows = history[-4:]
            baseline_volume = sum(max(self._safe_float(row.get("volume"), 0.0), 0.0) for row in baseline_rows) / 5.0
            latest_four_volumes = [max(self._safe_float(row.get("volume"), 0.0), 0.0) for row in review_rows]
            latest_four_average = sum(latest_four_volumes) / 4.0
            elevated = [baseline_volume > 0 and volume >= (2.0 * baseline_volume) for volume in latest_four_volumes]
            elevated_day_count = sum(1 for is_elevated in elevated if is_elevated)
            persistent = baseline_volume > 0 and elevated_day_count >= 3 and latest_four_average >= (2.0 * baseline_volume)
            if baseline_volume <= 0:
                no_baseline_count += 1

            activity_start_offset = next((index for index, is_elevated in enumerate(elevated) if is_elevated), 0)
            activity_start_index = len(history) - 4 + activity_start_offset
            starting_row = history[activity_start_index - 1]
            latest_row = history[-1]
            starting_oi = max(self._safe_float(starting_row.get("open_interest"), 0.0), 0.0)
            latest_oi = max(self._safe_float(latest_row.get("open_interest"), 0.0), 0.0)
            oi_change = latest_oi - starting_oi
            oi_change_percent = ((oi_change / starting_oi) * 100.0) if starting_oi > 0 else None
            oi_build_threshold = starting_oi * 1.25
            confirmation_date = ""
            if persistent and starting_oi > 0:
                for index in range(activity_start_index, len(history) - 1):
                    current_oi = max(self._safe_float(history[index].get("open_interest"), 0.0), 0.0)
                    next_oi = max(self._safe_float(history[index + 1].get("open_interest"), 0.0), 0.0)
                    if current_oi >= oi_build_threshold and next_oi >= oi_build_threshold:
                        confirmation_date = str(history[index + 1].get("snapshot_date") or "")
                        break
            strong_position_building = (
                persistent
                and starting_oi > 0
                and latest_oi >= oi_build_threshold
                and bool(confirmation_date)
            )
            classification = (
                "STRONG_POSITION_BUILDING" if strong_position_building
                else "PERSISTENT_UNUSUAL_ACTIVITY" if persistent
                else "NO_PERSISTENT_ACTIVITY"
            )
            ready_contract_count += 1
            activity_rows.append(
                {
                    "underlying": normalized_symbol,
                    "expiry": expiry,
                    "strike": strike,
                    "optionType": side,
                    "priorFiveDayAverageVolume": round(baseline_volume, 2),
                    "latestFourDailyVolumes": [
                        {
                            "date": str(row.get("snapshot_date") or ""),
                            "volume": round(volume),
                            "elevated": elevated[index],
                        }
                        for index, (row, volume) in enumerate(zip(review_rows, latest_four_volumes))
                    ],
                    "latestFourDayAverageVolume": round(latest_four_average, 2),
                    "elevatedDayCount": elevated_day_count,
                    "activityStartDate": str(history[activity_start_index].get("snapshot_date") or ""),
                    "startingOiDate": str(starting_row.get("snapshot_date") or ""),
                    "startingOi": round(starting_oi),
                    "latestOiDate": str(latest_row.get("snapshot_date") or ""),
                    "latestOi": round(latest_oi),
                    "oiChange": round(oi_change),
                    "oiChangePercent": round(oi_change_percent, 2) if oi_change_percent is not None else None,
                    "oiConfirmationDate": confirmation_date or None,
                    "classification": classification,
                    "baselineRatio": round((latest_four_average / baseline_volume), 3) if baseline_volume > 0 else None,
                }
            )

        classification_rank = {
            "STRONG_POSITION_BUILDING": 0,
            "PERSISTENT_UNUSUAL_ACTIVITY": 1,
            "NO_PERSISTENT_ACTIVITY": 2,
        }
        activity_rows.sort(
            key=lambda row: (
                classification_rank.get(str(row.get("classification")), 9),
                -self._safe_float(row.get("baselineRatio"), 0.0),
                -self._safe_float(row.get("oiChangePercent"), -999.0),
                str(row.get("expiry") or ""),
                -self._safe_float(row.get("strike"), 0.0),
            )
        )
        signals = [
            row for row in activity_rows
            if row.get("classification") in {"STRONG_POSITION_BUILDING", "PERSISTENT_UNUSUAL_ACTIVITY"}
        ]
        return {
            "symbol": normalized_symbol,
            "requiredSnapshotDays": required_days,
            "snapshotDays": dates,
            "availableSnapshotDays": len(dates),
            "additionalSnapshotDaysNeeded": max(0, required_days - len(dates)),
            "trackedContractCount": len(records_by_contract),
            "readyContractCount": ready_contract_count,
            "waitingContractCount": waiting_contract_count,
            "noBaselineVolumeCount": no_baseline_count,
            "strongPositionBuildingCount": sum(1 for row in signals if row.get("classification") == "STRONG_POSITION_BUILDING"),
            "persistentUnusualActivityCount": sum(1 for row in signals if row.get("classification") == "PERSISTENT_UNUSUAL_ACTIVITY"),
            "signals": signals,
            "contracts": activity_rows,
            "directionNote": "Volume and open interest are classified without a bullish or bearish directional inference.",
        }

    def _record_oi_finder_live_wall_snapshot(
        self,
        symbol: str,
        chain_payload: dict,
        captured_at: datetime,
        include_history: bool = True,
    ) -> dict:
        """Persist the leading 0-14 DTE walls in five-minute buckets.

        Open interest is normally refreshed only once per day.  The companion
        volume strength therefore captures new live option activity, while the
        wall strength remains the structural gamma-weighted OI level.
        """
        levels_by_side: dict[str, dict[float, dict]] = {"CALL": {}, "PUT": {}}
        has_gamma = False
        for side in ("CALL", "PUT"):
            for contract in self._option_chain_contracts(chain_payload, side):
                days_to_expiration = int(self._safe_float(contract.get("daysToExpiration"), -1))
                if not 0 <= days_to_expiration <= OI_FINDER_MAX_DAYS_TO_EXPIRATION:
                    continue
                strike = self._safe_float(contract.get("strike_price"), 0.0)
                if strike <= 0:
                    continue
                gamma = abs(self._safe_float(contract.get("gamma"), 0.0))
                volume = max(self._safe_float(contract.get("total_volume"), 0.0), 0.0)
                open_interest = max(self._safe_float(contract.get("open_interest"), 0.0), 0.0)
                if gamma > 0:
                    has_gamma = True
                level = levels_by_side[side].setdefault(
                    strike,
                    {
                        "side": side,
                        "strike": strike,
                        "gamma_oi": 0.0,
                        "gamma_volume": 0.0,
                        "volume": 0.0,
                        "open_interest": 0.0,
                    },
                )
                level["gamma_oi"] += gamma * open_interest
                level["gamma_volume"] += gamma * volume
                level["volume"] += volume
                level["open_interest"] += open_interest

        if not any(levels_by_side.values()):
            return {
                "symbol": str(symbol or "").upper(),
                "scope": "0-31 DTE leading option walls",
                "intervalMinutes": 5,
                "retentionDays": 183,
                "historyDays": 7,
                "metricLabel": "Gamma x OI",
                "series": [],
                "summary": {},
            }

        metric_label = "Gamma x OI" if has_gamma else "Open interest"
        leading_levels: list[dict] = []
        for side, levels in levels_by_side.items():
            for level in levels.values():
                # A provider may omit Greeks for an entire chain. In that
                # case retain a useful OI wall rather than showing no chart.
                level["wall_strength"] = level["gamma_oi"] if has_gamma else level["open_interest"]
                level["volume_strength"] = level["gamma_volume"] if has_gamma else level["volume"]
            leading_levels.extend(
                sorted(
                    levels.values(),
                    key=lambda item: (item["wall_strength"], item["volume_strength"]),
                    reverse=True,
                )[:3]
            )

        utc_time = captured_at.astimezone(timezone.utc)
        bucket_time = int(utc_time.timestamp() // 300 * 300)
        self.repository.upsert_option_wall_strength_snapshots([
            {
                "bucket_time": bucket_time,
                "captured_at": utc_time.isoformat(),
                "symbol": symbol,
                "side": level["side"],
                "strike": level["strike"],
                "wall_strength": level["wall_strength"],
                "volume_strength": level["volume_strength"],
                "volume": level["volume"],
                "open_interest": level["open_interest"],
            }
            for level in leading_levels
        ])

        # The paced collector only needs to persist the current bucket. Reading
        # and rebuilding seven days of chart series for every MAG7 ticker is
        # display work and should happen only for an interactive Finder request.
        if not include_history:
            return {
                "symbol": str(symbol or "").upper(),
                "scope": "0-31 DTE leading option walls",
                "intervalMinutes": 5,
                "retentionDays": 183,
                "historyDays": 7,
                "metricLabel": metric_label,
                "series": [],
                "summary": {},
            }

        selected_keys = {(str(level["side"]), round(self._safe_float(level["strike"], 0.0), 4)) for level in leading_levels}
        grouped_rows: dict[tuple[str, float], list[dict]] = {key: [] for key in selected_keys}
        for row in self.repository.option_wall_strength_snapshots(symbol, days=7):
            key = (str(row.get("side") or "").upper(), round(self._safe_float(row.get("strike"), 0.0), 4))
            if key in grouped_rows:
                grouped_rows[key].append(row)

        series: list[dict] = []
        summary = {
            "callWallStrength": 0.0,
            "putWallStrength": 0.0,
            "callFlowStrength": 0.0,
            "putFlowStrength": 0.0,
        }
        for level in leading_levels:
            side = str(level["side"])
            strike = round(self._safe_float(level["strike"], 0.0), 4)
            rows = sorted(grouped_rows.get((side, strike), []), key=lambda row: int(row.get("bucket_time") or 0))
            points: list[dict] = []
            prior: dict | None = None
            for row in rows:
                bucket = int(row.get("bucket_time") or 0)
                wall_strength = max(self._safe_float(row.get("wall_strength"), 0.0), 0.0)
                volume_strength = max(self._safe_float(row.get("volume_strength"), 0.0), 0.0)
                elapsed = bucket - int(prior.get("bucket_time") or 0) if prior else 0
                flow_strength = (
                    max(volume_strength - self._safe_float(prior.get("volume_strength"), 0.0), 0.0)
                    if prior and 0 < elapsed <= 15 * 60
                    else 0.0
                )
                points.append(
                    {
                        "time": bucket,
                        "wallStrength": round(wall_strength, 4),
                        "flowStrength": round(flow_strength, 4),
                        "volume": round(max(self._safe_float(row.get("volume"), 0.0), 0.0)),
                        "openInterest": round(max(self._safe_float(row.get("open_interest"), 0.0), 0.0)),
                    }
                )
                prior = row
            latest = points[-1] if points else {"wallStrength": level["wall_strength"], "flowStrength": 0.0}
            prefix = "call" if side == "CALL" else "put"
            summary[f"{prefix}WallStrength"] += self._safe_float(latest.get("wallStrength"), 0.0)
            summary[f"{prefix}FlowStrength"] += self._safe_float(latest.get("flowStrength"), 0.0)
            series.append(
                {
                    "key": f"{side}|{strike:.4f}",
                    "side": side,
                    "strike": strike,
                    "points": points,
                }
            )

        call_wall = summary["callWallStrength"]
        put_wall = summary["putWallStrength"]
        call_flow = summary["callFlowStrength"]
        put_flow = summary["putFlowStrength"]
        summary.update(
            {
                "dominantWallSide": "CALL" if call_wall > put_wall else "PUT" if put_wall > call_wall else "BALANCED",
                "dominantFlowSide": "CALL" if call_flow > put_flow else "PUT" if put_flow > call_flow else "BALANCED",
            }
        )
        return {
            "symbol": str(symbol or "").upper(),
            "scope": "0-31 DTE leading option walls",
            "intervalMinutes": 5,
            "retentionDays": 183,
            "historyDays": 7,
            "metricLabel": metric_label,
            "series": series,
            "summary": {key: round(value, 4) if isinstance(value, float) else value for key, value in summary.items()},
        }

    def _oi_finder_chain_disk_path(self, symbol: str) -> Path | None:
        target = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            return None
        return Path(self.oi_finder_chain_disk_cache_dir) / f"{target}.json.gz"

    def _load_oi_finder_chain_disk_payload(self, symbol: str) -> dict | None:
        """Load the last usable compact chain so restarts never show a spinner."""
        path = self._oi_finder_chain_disk_path(symbol)
        if path is None:
            return None
        try:
            age_seconds = max(0.0, time.time() - path.stat().st_mtime)
            if age_seconds > OI_FINDER_CHAIN_DISK_CACHE_MAX_AGE_SECONDS:
                return None
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or not payload.get("symbol"):
                return None
            if payload.get("optionQuoteSchemaVersion") != OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION:
                return None
            return {
                **payload,
                "diskCached": True,
                "diskCacheAgeSeconds": round(age_seconds, 2),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _save_oi_finder_chain_disk_payload(self, symbol: str, payload: dict) -> None:
        """Atomically persist only the compact browser feed, never raw chains."""
        if not isinstance(payload, dict) or not payload.get("live"):
            return
        path = self._oi_finder_chain_disk_path(symbol)
        if path is None:
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            persisted_payload = {
                **payload,
                "optionQuoteSchemaVersion": OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION,
            }
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
                json.dump(persisted_payload, handle, separators=(",", ":"), default=str)
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _slim_initial_oi_finder_chain_payload(payload: dict) -> dict:
        """Keep only the nearest expiry for the latency-sensitive first paint."""
        if not isinstance(payload, dict):
            return payload
        current_atm = payload.get("currentAtm") if isinstance(payload.get("currentAtm"), dict) else {}
        front_expiry = str(current_atm.get("expiry") or "")[:10]
        if not front_expiry:
            candidates = [str(item or "")[:10] for item in payload.get("expiries") or [] if str(item or "")]
            front_expiry = min(candidates) if candidates else ""
        if not front_expiry:
            return {**payload, "frontExpiryOnly": True}

        def matching(rows: object) -> list:
            return [
                row
                for row in (rows if isinstance(rows, list) else [])
                if str((row or {}).get("expiry") or "")[:10] == front_expiry
            ]

        expected_moves = payload.get("expiryExpectedMoves")
        slim = {
            **payload,
            "expiries": [front_expiry],
            "callRows": matching(payload.get("callRows")),
            "putRows": matching(payload.get("putRows")),
            "selectedExpiryChainRows": matching(payload.get("selectedExpiryChainRows")),
            "tosScriptLevels": matching(payload.get("tosScriptLevels")),
            "expiryExpectedMoves": (
                {front_expiry: expected_moves.get(front_expiry)}
                if isinstance(expected_moves, dict) and front_expiry in expected_moves
                else {}
            ),
            "frontExpiryOnly": True,
        }
        return slim

    def _refresh_oi_finder_in_background(self, symbol: str, compact: bool = False) -> None:
        """Refresh an older Finder cache without making the page wait on a broker call."""
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            return
        # Compact and full refreshes populate different caches, so they must
        # not suppress each other through one shared in-flight set.
        refresh_key = f"{normalized_symbol}|compact" if compact else normalized_symbol
        with self.oi_finder_lock:
            if refresh_key in self.oi_finder_background_refreshes:
                return
            self.oi_finder_background_refreshes.add(refresh_key)

        def refresh() -> None:
            try:
                self.oi_finder_payload(normalized_symbol, force=True, compact=compact)
            finally:
                with self.oi_finder_lock:
                    self.oi_finder_background_refreshes.discard(refresh_key)

        threading.Thread(
            target=refresh,
            name=f"oi-finder-refresh-{normalized_symbol.lower()}",
            daemon=True,
        ).start()

    def oi_finder_payload(
        self,
        symbol: str,
        force: bool = False,
        compact: bool = False,
        initial_paint: bool = False,
        background_snapshot: bool = False,
    ) -> dict:
        """Return one ticker's 0-14 DTE call/put liquidity comparison.

        ``compact=True`` serves the chart/chain panel's fast path: the live
        chain, ATM, expiries and expected moves only. It skips the DB-backed
        Finder analytics (daily liquidity heatmap, unusual-OTM dashboard over
        183 days of snapshots, persistence and live-wall recording), which
        measured ~3,200 sqlite queries and ~6.8s per request — paid on every
        cold open AND on every 15s revalidation, for data the chart never
        shows.
        """
        # A browser chain read is interactive work and pauses background
        # scanners. Scheduled snapshot collection must not extend that window
        # itself, otherwise the worker continuously grants itself priority.
        if not background_snapshot:
            self.touch_oi_finder_interactive_window()
        normalized_symbol = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", normalized_symbol):
            return {
                "live": False,
                "symbol": normalized_symbol,
                "errors": [{"error": "Enter a valid ticker symbol."}],
                "callRows": [],
                "putRows": [],
            }

        now = datetime.now().astimezone()
        stale_payload = None
        # Compact chain responses are cached separately: a compact payload must
        # never be served to the Finder (it has no analytics), and the Finder's
        # slower full payload must not delay the chart's chain panel.
        payload_cache = self.oi_finder_chain_cache if compact else self.oi_finder_cache
        with self.oi_finder_lock:
            cached = payload_cache.get(normalized_symbol)
            if cached and not force:
                cache_age = (now - cached[0]).total_seconds()
                needs_full_analytics = (
                    not compact
                    and not background_snapshot
                    and bool(cached[1].get("analyticsDeferred"))
                )
                if cache_age < 15 and not needs_full_analytics:
                    ready_payload = {**cached[1], "cached": True, "refreshing": False}
                    return (
                        self._slim_initial_oi_finder_chain_payload(ready_payload)
                        if compact and initial_paint
                        else ready_payload
                    )
                # The visible Finder should always keep showing its last complete
                # chain while a slow broker request runs. A fresh response replaces
                # this cache as soon as it arrives; it is never used for execution.
                stale_payload = {
                    **cached[1],
                    "cached": True,
                    "stale": True,
                    "refreshing": True,
                }
        if stale_payload is not None:
            self._refresh_oi_finder_in_background(normalized_symbol, compact=compact)
            return (
                self._slim_initial_oi_finder_chain_payload(stale_payload)
                if compact and initial_paint
                else stale_payload
            )

        # The compact cache survives backend restarts. Serve it immediately
        # and revalidate off-thread instead of making the chart/options page
        # wait on Schwab before it can paint.
        if compact and not force:
            disk_payload = self._load_oi_finder_chain_disk_payload(normalized_symbol)
            if disk_payload is not None:
                with self.oi_finder_lock:
                    payload_cache[normalized_symbol] = (now - timedelta(seconds=16), disk_payload)
                self._refresh_oi_finder_in_background(normalized_symbol, compact=True)
                disk_response = {
                    **disk_payload,
                    "cached": True,
                    "stale": True,
                    "refreshing": True,
                }
                return (
                    self._slim_initial_oi_finder_chain_payload(disk_response)
                    if initial_paint
                    else disk_response
                )

        chain_payload: dict = {}
        underlying_quote: dict = {}
        source = "Schwab/TOS option chain"
        fallback_note = ""
        try:
            # OI Finder is intentionally a direct, single-symbol Schwab/TOS
            # request. It must not depend on the app-wide stock data provider
            # (which may remain Alpaca for charting/scanning).
            market_client = SchwabClient()
            if not market_client.configured:
                raise RuntimeError("Schwab/TOS option chain is not configured.")
            chain_payload = market_client.get_option_chain(
                normalized_symbol,
                contract_type="ALL",
                strike_count=40 if compact and initial_paint else 80,
                # OI Finder only displays the next 0–31 DTE.  Requesting every
                # listed expiry first can make a new ticker search needlessly
                # slow, especially for liquid names with long-dated LEAPS.
                from_date=now,
                to_date=now + timedelta(
                    days=7 if compact and initial_paint else OI_FINDER_MAX_DAYS_TO_EXPIRATION
                ),
            )
            if not chain_payload and compact and initial_paint:
                # Monthly-only names can have no expiry in the first week.
                # Retry the same narrow strike window across 31 DTE before
                # considering the provider unavailable.
                chain_payload = market_client.get_option_chain(
                    normalized_symbol,
                    contract_type="ALL",
                    strike_count=40,
                    from_date=now,
                    to_date=now + timedelta(days=OI_FINDER_MAX_DAYS_TO_EXPIRATION),
                )
            if not chain_payload:
                raise RuntimeError("Schwab/TOS returned an empty option chain.")
            underlying_quote = market_client.get_quotes([normalized_symbol]).get(normalized_symbol, {})
        except Exception:
            tradier_client = TradierClient()
            source = "Tradier option chain (Schwab/TOS fallback)"
            fallback_note = (
                "Schwab/TOS chain was unavailable, so the finder used the live Tradier chain. "
                "Expected Move is the nearest ATM call + put midpoint and can differ from TOS's volatility-based move."
            )
            try:
                chain_payload = tradier_client.get_option_chain_range(
                    normalized_symbol,
                    contract_type="ALL",
                    strike_count=40 if compact and initial_paint else 80,
                    max_days_to_expiration=(
                        7 if compact and initial_paint else OI_FINDER_MAX_DAYS_TO_EXPIRATION
                    ),
                )
            except Exception as exc:
                return {
                    "live": False,
                    "symbol": normalized_symbol,
                    "source": source,
                    "scannedAt": _serialize_value(now),
                    "errors": [{"error": str(exc)}],
                    "callRows": [],
                    "putRows": [],
                }

        call_rows = self._oi_finder_side_rows(chain_payload, "CALL")
        put_rows = self._oi_finder_side_rows(chain_payload, "PUT")
        selected_expiry_chain_rows = self._oi_finder_selected_expiry_chain_rows(chain_payload)
        current_atm = self._oi_finder_current_atm(chain_payload)
        tos_script_levels = self._oi_finder_tos_script_levels(chain_payload)
        # Keep expected move tied to each expiry.  The Finder heatmap uses
        # these live option-chain values as column context, rather than
        # applying the nearest expiry's move to every week.
        chain_expiries = {
            str(contract.get("expiry_date") or "")[:10]
            for option_side in ("CALL", "PUT")
            for contract in self._option_chain_contracts(chain_payload, option_side)
            if str(contract.get("expiry_date") or "")[:10]
        }
        expiry_expected_moves = {
            expiry: self._expected_move_for_expiry(chain_payload, expiry)
            for expiry in sorted(chain_expiries)
        }
        # Everything below this point is Finder-only analytics backed by the
        # snapshot database. The chart's chain panel never displays it, so the
        # compact path skips it entirely rather than paying thousands of
        # sqlite queries per request.
        if compact:
            volume_momentum = {}
            daily_liquidity_heatmap = {}
            unusual_otm_activity = {}
            unusual_otm_dashboard = {}
            persistent_activity = {}
            live_wall_trend = {}
        else:
            volume_snapshot = self._oi_finder_volume_snapshot(current_atm, call_rows, put_rows, now, chain_payload)
            volume_momentum = self._oi_finder_volume_momentum(normalized_symbol, volume_snapshot)
            self._attach_oi_finder_volume_momentum(current_atm, call_rows, put_rows, volume_momentum)
            daily_liquidity_heatmap = self._record_oi_finder_daily_chain_snapshot(
                normalized_symbol,
                chain_payload,
                current_atm,
                now,
                include_history=not background_snapshot,
            )
            live_wall_trend = self._record_oi_finder_live_wall_snapshot(
                normalized_symbol,
                chain_payload,
                now,
                include_history=not background_snapshot,
            )
            if background_snapshot:
                # These fields are needed only by the visible Finder. Avoid its
                # thousands of historical DB reads in the continuous collector.
                unusual_otm_activity = {}
                unusual_otm_dashboard = {}
                persistent_activity = {}
            else:
                unusual_otm_activity = self._oi_finder_unusual_otm_activity(
                    normalized_symbol,
                    chain_payload,
                    current_atm,
                )
                unusual_otm_dashboard = build_dashboard_response(
                    normalized_symbol,
                    self._option_underlying_price_from_chain(chain_payload),
                    chain_payload,
                    self.repository.option_chain_daily_snapshots(normalized_symbol, days=183),
                    now.date(),
                )
                persistent_activity = self._oi_finder_persistent_option_activity(normalized_symbol)
        underlying_move = self._option_underlying_day_move(chain_payload, underlying_quote)
        implied_volatility = self._chain_implied_volatility(chain_payload)
        call_liquidity = sum(self._safe_float(row.get("liquidity_score"), 0.0) for row in call_rows)
        put_liquidity = sum(self._safe_float(row.get("liquidity_score"), 0.0) for row in put_rows)
        combined_liquidity = call_liquidity + put_liquidity
        call_pct = (call_liquidity / combined_liquidity * 100.0) if combined_liquidity > 0 else 50.0
        put_pct = (put_liquidity / combined_liquidity * 100.0) if combined_liquidity > 0 else 50.0
        if call_liquidity >= put_liquidity * 1.08 and call_liquidity > 0:
            stronger_side = "CALL WALLS STRONGER"
            market_read = "Call resistance dominates - puts / caution"
        elif put_liquidity >= call_liquidity * 1.08 and put_liquidity > 0:
            stronger_side = "PUT WALLS STRONGER"
            market_read = "Put support dominates - calls favored"
        else:
            stronger_side = "BALANCED"
            market_read = "Call and put liquidity are closely matched"
        expiries = sorted({row["expiry"] for row in call_rows + put_rows})
        payload = {
            "live": bool(call_rows or put_rows),
            "optionQuoteSchemaVersion": OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION,
            "cached": False,
            "analyticsDeferred": bool(background_snapshot),
            "symbol": normalized_symbol,
            "source": source,
            "sourceNote": fallback_note,
            "scannedAt": _serialize_value(now),
            "refreshSeconds": 15,
            "maxDaysToExpiration": OI_FINDER_MAX_DAYS_TO_EXPIRATION,
            "minDelta": 0.20,
            "snapshotSchedule": self._oi_finder_snapshot_schedule_payload(),
            "underlyingPrice": underlying_move["price"] or round(self._option_underlying_price_from_chain(chain_payload), 4),
            "todayChange": underlying_move["change"],
            "todayChangePercent": underlying_move["changePercent"],
            "impliedVolatility": round(implied_volatility * 100.0, 4) if implied_volatility is not None else None,
            "expectedMoveMethod": (
                "0DTE / 1DTE live ATM straddle; later expiries use their own live ATM IV"
                if implied_volatility is not None
                else "Live ATM straddle expected move"
            ),
            "expiries": expiries,
            "expiryExpectedMoves": expiry_expected_moves,
            "currentAtm": current_atm,
            "selectedExpiryChainRows": selected_expiry_chain_rows,
            "tosScriptLevels": tos_script_levels,
            "volumeMomentum": volume_momentum,
            "dailyLiquidityHeatmap": daily_liquidity_heatmap,
            "unusualOtmActivity": unusual_otm_activity,
            "unusualOtmDashboard": unusual_otm_dashboard,
            "persistentActivity": persistent_activity,
            "liveWallTrend": live_wall_trend,
            "callRows": call_rows,
            "putRows": put_rows,
            "summary": {
                "strongerSide": stronger_side,
                "marketRead": market_read,
                "callLiquidity": round(call_liquidity),
                "putLiquidity": round(put_liquidity),
                "callPercent": round(call_pct, 1),
                "putPercent": round(put_pct, 1),
            },
            "errors": [],
        }
        if compact and initial_paint:
            payload = self._slim_initial_oi_finder_chain_payload(payload)
        with self.oi_finder_lock:
            # Timestamp at completion, not request start.  A slow broker fetch
            # must not consume the cache's usable lifetime before the user even
            # sees the completed chain.
            payload_cache[normalized_symbol] = (datetime.now().astimezone(), payload)
        if compact:
            self._save_oi_finder_chain_disk_payload(normalized_symbol, payload)
            if initial_paint:
                # Let the small response leave the socket before expanding to
                # the remaining 31-DTE expiries in the background.
                timer = threading.Timer(
                    OI_FINDER_CHART_FULL_REFRESH_DEFER_SECONDS,
                    self._refresh_oi_finder_in_background,
                    args=(normalized_symbol, True),
                )
                timer.daemon = True
                timer.start()
        return payload

    def mag7_oi_wall_payload(self, force: bool = False) -> dict:
        """Return an ungated nearest-expiry OI wall snapshot, including 0DTE, for the standard Mag7."""
        now = datetime.now().astimezone()
        with self.mag7_oi_wall_lock:
            cache_age = (
                (now - self.mag7_oi_wall_cache_timestamp).total_seconds()
                if self.mag7_oi_wall_cache_timestamp is not None
                else None
            )
            if not force and self.mag7_oi_wall_cache is not None and cache_age is not None and cache_age < 15:
                return {**self.mag7_oi_wall_cache, "cached": True}

            tradier_client = TradierClient()
            if not tradier_client.configured:
                return {
                    "source": "Tradier option chain",
                    "live": False,
                    "scannedAt": _serialize_value(now),
                    "refreshSeconds": 15,
                    "symbols": list(MAGNIFICENT_SEVEN),
                    "rows": [],
                    "errors": [{"symbol": "ALL", "error": "TRADIER_ACCESS_TOKEN is not configured."}],
                }

            chain_payloads: dict[str, dict] = {}
            errors: list[dict] = []
            with ThreadPoolExecutor(max_workers=6) as executor:
                future_map = {
                    executor.submit(
                        tradier_client.get_option_chain,
                        symbol,
                        contract_type="ALL",
                        strike_count=80,
                    ): symbol
                    for symbol in MAGNIFICENT_SEVEN
                }
                for future in as_completed(future_map):
                    symbol = future_map[future]
                    try:
                        payload = future.result()
                    except Exception as exc:
                        errors.append({"symbol": symbol, "error": str(exc)})
                        continue
                    if payload:
                        chain_payloads[symbol] = payload
                    else:
                        errors.append({"symbol": symbol, "error": "Option chain payload was empty."})

            rows: list[dict] = []
            for symbol in MAGNIFICENT_SEVEN:
                chain_payload = chain_payloads.get(symbol)
                if not chain_payload:
                    continue
                underlying_price = self._option_underlying_price_from_chain(chain_payload)
                calls = self._option_chain_contracts(chain_payload, "CALL")
                puts = self._option_chain_contracts(chain_payload, "PUT")
                expiry_candidates: list[tuple[int, str]] = []
                for contract in calls:
                    expiry = str(contract.get("expiry_date") or "")
                    raw_days_to_expiration = contract.get("daysToExpiration")
                    if raw_days_to_expiration is None:
                        continue
                    days_to_expiration = int(raw_days_to_expiration)
                    if expiry and days_to_expiration >= 0:
                        expiry_candidates.append((days_to_expiration, expiry))
                if not expiry_candidates:
                    errors.append({"symbol": symbol, "error": "No current or future option expiry was available."})
                    continue
                days_to_expiration, expiry = min(expiry_candidates, key=lambda item: (item[0], item[1]))
                expiry_calls = [item for item in calls if str(item.get("expiry_date") or "") == expiry]
                expiry_puts = [item for item in puts if str(item.get("expiry_date") or "") == expiry]
                strike_candidates = [
                    self._safe_float(item.get("strike_price"), 0.0)
                    for item in expiry_calls
                    if self._safe_float(item.get("strike_price"), 0.0) > 0
                ]
                atm_strike = min(strike_candidates, key=lambda strike: abs(strike - underlying_price)) if strike_candidates else 0.0
                wall_plan = self._option_oi_wall_plan(chain_payload, expiry, atm_strike)
                if not wall_plan:
                    errors.append({"symbol": symbol, "error": "OI wall data was unavailable for the nearest expiry."})
                    continue
                call_total_oi = sum(self._safe_float(item.get("open_interest"), 0.0) for item in expiry_calls)
                call_total_volume = sum(self._safe_float(item.get("total_volume"), 0.0) for item in expiry_calls)
                put_total_oi = sum(self._safe_float(item.get("open_interest"), 0.0) for item in expiry_puts)
                put_total_volume = sum(self._safe_float(item.get("total_volume"), 0.0) for item in expiry_puts)
                call_wall_levels = self._option_oi_wall_levels(
                    expiry_calls, underlying_price, atm_strike, "CALL"
                )
                put_wall_levels = self._option_oi_wall_levels(
                    expiry_puts, underlying_price, atm_strike, "PUT"
                )
                rows.append(
                    {
                        "underlying": symbol,
                        "underlying_price": round(underlying_price, 4),
                        "expiry": expiry,
                        "days_to_expiration": days_to_expiration,
                        "atm_strike": atm_strike or None,
                        **wall_plan,
                        "call_wall_levels": call_wall_levels,
                        "put_wall_levels": put_wall_levels,
                        "call_total_open_interest": call_total_oi,
                        "call_total_volume": call_total_volume,
                        "put_total_open_interest": put_total_oi,
                        "put_total_volume": put_total_volume,
                        "scanned_at": _serialize_value(now),
                    }
                )

            payload = {
                "source": "Tradier option chain",
                "live": bool(rows),
                "cached": False,
                "scannedAt": _serialize_value(now),
                "refreshSeconds": 15,
                "symbols": list(MAGNIFICENT_SEVEN),
                "rows": rows,
                "errors": errors,
            }
            self.mag7_oi_wall_cache = payload
            self.mag7_oi_wall_cache_timestamp = now
            return payload

    def _option_liquidity_strike_plan(self, chain_payload: dict, expiry_date: str) -> dict:
        underlying_price = self._option_underlying_price_from_chain(chain_payload)
        calls = [
            item
            for item in self._option_chain_contracts(chain_payload, "CALL")
            if str(item.get("expiry_date")) == str(expiry_date)
            and self._safe_float(item.get("strike_price"), 0.0) > 0
        ]
        if underlying_price <= 0 or not calls:
            return {}

        floor_calls = [
            item for item in calls
            if self._safe_float(item.get("strike_price"), 0.0) <= underlying_price
        ]
        if floor_calls:
            atm_contract = max(
                floor_calls,
                key=lambda item: self._safe_float(item.get("strike_price"), 0.0),
            )
        else:
            atm_contract = min(
                calls,
                key=lambda item: self._safe_float(item.get("strike_price"), 0.0),
            )
        atm_strike = self._safe_float(atm_contract.get("strike_price"), 0.0)
        wall_plan = self._option_oi_wall_plan(chain_payload, expiry_date, atm_strike)
        otm_calls = [
            item
            for item in calls
            if self._safe_float(item.get("strike_price"), 0.0) > max(underlying_price, atm_strike)
        ]
        if not otm_calls:
            return {
                "underlying_price": round(underlying_price, 4),
                "atm_strike": atm_strike,
                "atm_volume": self._safe_float(atm_contract.get("total_volume"), 0.0),
                "atm_open_interest": self._safe_float(atm_contract.get("open_interest"), 0.0),
                "atm_liquidity_score": round(self._option_liquidity_score(atm_contract), 2),
                "breakout_level": atm_strike,
                "breakout_passed": underlying_price >= atm_strike,
                **wall_plan,
            }

        target_contract = max(
            otm_calls,
            key=lambda item: (
                self._option_liquidity_score(item),
                self._option_combined_liquidity_score(item),
                -abs(self._safe_float(item.get("strike_price"), 0.0) - underlying_price),
            ),
        )
        atm_volume = self._safe_float(atm_contract.get("total_volume"), 0.0)
        atm_open_interest = self._safe_float(atm_contract.get("open_interest"), 0.0)
        target_volume = self._safe_float(target_contract.get("total_volume"), 0.0)
        target_open_interest = self._safe_float(target_contract.get("open_interest"), 0.0)
        atm_liquidity_dominates_otm = atm_volume > target_volume and atm_open_interest > target_open_interest
        return {
            "underlying_price": round(underlying_price, 4),
            "atm_strike": atm_strike,
            "atm_volume": atm_volume,
            "atm_open_interest": atm_open_interest,
            "atm_liquidity_score": round(self._option_liquidity_score(atm_contract), 2),
            "atm_liquidity_dominates_otm": atm_liquidity_dominates_otm,
            "breakout_level": atm_strike,
            "breakout_passed": underlying_price >= atm_strike,
            "breakout_required": atm_liquidity_dominates_otm,
            "target_strike": self._safe_float(target_contract.get("strike_price"), 0.0),
            "target_volume": target_volume,
            "target_open_interest": target_open_interest,
            "target_liquidity_score": round(self._option_liquidity_score(target_contract), 2),
            "target_liquidity_metric": "volume" if target_volume >= target_open_interest else "open_interest",
            **wall_plan,
        }

    def _contract_mid_price(self, contract: dict) -> float:
        bid = float(contract.get("bid") or 0.0)
        ask = float(contract.get("ask") or 0.0)
        last = float(contract.get("mark") or contract.get("last") or 0.0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2.0, 4)
        return round(last, 4) if last > 0 else 0.0

    def _option_spread_allowed(self, spread: float, mid_price: float) -> bool:
        raw = str(self.option_bot_config.get("spreadFilter") or "").strip().lower()
        if not raw:
            return True
        value = self._parse_numeric_guardrail(raw, None)
        if value is None or value <= 0:
            return True
        spread_pct = ((spread / mid_price) * 100.0) if mid_price > 0 else None
        if "%" in raw or value >= 1:
            if mid_price <= 0:
                return False
            return spread_pct <= value
        if "$" in raw or "dollar" in raw:
            return spread <= value
        if mid_price <= 0:
            return spread <= value
        # Backward-compatible adaptive mode for legacy values like "0.05":
        # pass either tight low-premium spreads or high-premium contracts with
        # reasonable percent spread, e.g. AVGO/MSTR-style chains.
        return spread <= value or spread_pct <= DEFAULT_OPTION_ADAPTIVE_SPREAD_PERCENT
        return spread <= value

    def _expected_move_for_expiry(self, chain_payload: dict, expiry_date: str) -> float | None:
        calls = [item for item in self._option_chain_contracts(chain_payload, "CALL") if str(item.get("expiry_date")) == str(expiry_date)]
        puts = [item for item in self._option_chain_contracts(chain_payload, "PUT") if str(item.get("expiry_date")) == str(expiry_date)]
        if not calls or not puts:
            return None

        underlying_price = float(chain_payload.get("underlyingPrice") or 0.0)
        if underlying_price <= 0:
            underlying = chain_payload.get("underlying") or {}
            underlying_price = float((underlying or {}).get("last") or 0.0)
        if underlying_price <= 0:
            return None

        expiry_days = [
            int(self._safe_float(item.get("daysToExpiration"), 0.0))
            for item in calls + puts
            if item.get("daysToExpiration") is not None
        ]
        days_to_expiration = min(expiry_days) if expiry_days else 0

        call_by_strike = {
            float(item["strike_price"]): item
            for item in calls
            if item.get("strike_price") is not None
        }
        put_by_strike = {
            float(item["strike_price"]): item
            for item in puts
            if item.get("strike_price") is not None
        }
        common_strikes = sorted(set(call_by_strike).intersection(put_by_strike))
        if not common_strikes:
            return None
        strike = min(common_strikes, key=lambda value: abs(value - underlying_price))
        call_mid = self._contract_mid_price(call_by_strike[strike])
        put_mid = self._contract_mid_price(put_by_strike[strike])
        atm_straddle_move = round(call_mid + put_mid, 4) if call_mid > 0 and put_mid > 0 else None

        # For a live 0DTE contract, use the current nearest-ATM call + put
        # midpoint.  Applying a full calendar day to annual IV materially
        # overstates the remaining intraday move (for example SPY ±$11 versus
        # the live ATM straddle of about ±$3).
        if days_to_expiration <= 0 and atm_straddle_move is not None:
            return atm_straddle_move

        # For later expiries, the chain-level ``volatility`` field is not the
        # selected expiry's ATM IV.  For example, CRCL can return 29% at the
        # root while the live Jul-24 ATM contracts are about 102%.  Use the
        # nearest common ATM call/put's live IV instead, matching the per-
        # expiry view in thinkorswim rather than mixing expirations together.
        atm_iv_values: list[float] = []
        for contract in (call_by_strike[strike], put_by_strike[strike]):
            raw_iv = self._safe_float(
                contract.get(
                    "volatility",
                    contract.get("impliedVolatility", contract.get("implied_volatility", 0.0)),
                ),
                0.0,
            )
            if raw_iv > 0:
                atm_iv_values.append(raw_iv / 100.0 if raw_iv > 1.0 else raw_iv)
        implied_volatility = (sum(atm_iv_values) / len(atm_iv_values)) if atm_iv_values else None
        if implied_volatility is not None and implied_volatility > 0:
            # Schwab's DTE includes the current calendar date.  A later-expiry
            # TOS move is based on the remaining sessions, so exclude today.
            # A one-day expiry falls back to its live ATM straddle below.
            remaining_days = max(days_to_expiration - 1, 0)
            if remaining_days > 0:
                return round(underlying_price * implied_volatility * math.sqrt(remaining_days / 365.0), 4)
        return atm_straddle_move

    def _expiry_timestamp(self, expiry_value: str | None) -> pd.Timestamp | None:
        raw = str(expiry_value or "").strip()
        if not raw:
            return None
        try:
            timestamp = pd.Timestamp(raw)
        except Exception:
            return None
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is None:
            try:
                return timestamp.tz_localize(EASTERN_TZ)
            except Exception:
                return timestamp
        try:
            return timestamp.tz_convert(EASTERN_TZ)
        except Exception:
            return timestamp

    def _second_friday_cutoff(self, anchor: datetime) -> date:
        base_date = anchor.date()
        days_until_friday = (4 - base_date.weekday()) % 7
        first_friday = base_date + timedelta(days=days_until_friday)
        return first_friday + timedelta(days=7)

    def _oi_symbol_expiry_cutoff(self, chain_payload: dict, anchor: datetime) -> date | None:
        expiries: list[pd.Timestamp] = []
        for contract in self._option_chain_contracts(chain_payload, "CALL"):
            timestamp = self._expiry_timestamp(contract.get("expiry_date"))
            days_to_expiration = int(contract.get("daysToExpiration") or 0)
            if timestamp is None or days_to_expiration <= 0:
                continue
            expiries.append(timestamp.normalize())
        if not expiries:
            return None

        unique_expiries = sorted({item.date() for item in expiries})
        multi_weekly_window_end = anchor.date() + timedelta(days=10)
        near_term_expiries = [item for item in unique_expiries if item <= multi_weekly_window_end]
        near_term_weekdays = {item.weekday() for item in near_term_expiries}

        # Monday/Wednesday/Friday-style names should stop at the second Friday
        # window instead of bleeding into the following Monday expiry.
        if len(near_term_expiries) >= 4 and len(near_term_weekdays) >= 3:
            return self._second_friday_cutoff(anchor)

        return anchor.date() + timedelta(days=14)

    def _select_option_contract(self, symbol: str) -> tuple[dict | None, str]:
        if settings.market_data_provider != "schwab":
            return None, "real option contract selection requires Schwab/TOS market data provider"
        if not hasattr(self.market_data_client, "get_option_chain"):
            return None, "option chain client is not available"

        try:
            chain_payload = self.market_data_client.get_option_chain(symbol, contract_type="ALL", strike_count=80)
        except Exception as exc:
            return None, f"option chain request failed: {exc}"

        if not chain_payload:
            return None, "option chain returned no data"

        delta_cap = self._parse_numeric_guardrail(self.option_bot_config.get("deltaTarget"), DEFAULT_OPTION_DELTA_CAP)
        if delta_cap is None or delta_cap <= 0:
            delta_cap = DEFAULT_OPTION_DELTA_CAP
        if delta_cap > 1:
            delta_cap = delta_cap / 100.0

        min_expected_move = self._parse_numeric_guardrail(
            self.option_bot_config.get("expectedMove"),
            DEFAULT_OPTION_MIN_EXPECTED_MOVE,
        )
        if min_expected_move is None or min_expected_move <= 0:
            min_expected_move = DEFAULT_OPTION_MIN_EXPECTED_MOVE
        candidates: list[dict] = []
        liquidity_plan_by_expiry: dict[str, dict] = {}
        liquidity_breakout_blocks: list[dict] = []
        for contract in self._option_chain_contracts(chain_payload, "CALL"):
            delta = float(contract.get("delta") or 0.0)
            if delta <= 0 or delta > delta_cap:
                continue
            bid = float(contract.get("bid") or 0.0)
            ask = float(contract.get("ask") or 0.0)
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            expiry_date = str(contract.get("expiry_date") or "")
            expected_move = self._expected_move_for_expiry(chain_payload, expiry_date)
            if expected_move is None or expected_move < min_expected_move:
                continue
            if expiry_date not in liquidity_plan_by_expiry:
                liquidity_plan_by_expiry[expiry_date] = self._option_liquidity_strike_plan(chain_payload, expiry_date)
            liquidity_plan = liquidity_plan_by_expiry.get(expiry_date) or {}
            if liquidity_plan.get("breakout_required") and not liquidity_plan.get("breakout_passed"):
                liquidity_breakout_blocks.append(liquidity_plan)
                continue
            mid_price = self._contract_mid_price(contract)
            spread = ask - bid
            if not self._option_spread_allowed(spread, mid_price):
                continue
            contract_symbol = str(contract.get("symbol") or contract.get("description") or "").strip()
            if not contract_symbol or mid_price <= 0:
                continue
            broker_symbol = self.option_client.normalize_option_symbol(contract_symbol)
            candidates.append(
                {
                    "symbol": broker_symbol,
                    "source_symbol": contract_symbol,
                    "underlying": str(symbol).upper(),
                    "expiry_date": expiry_date,
                    "strike_price": float(contract.get("strike_price") or 0.0),
                    "delta": delta,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid_price,
                    "spread": round(spread, 4),
                    "spread_percent": round((spread / mid_price) * 100.0, 4) if mid_price > 0 else None,
                    "expected_move": expected_move,
                    "days_to_expiration": int(contract.get("daysToExpiration") or 0),
                    "total_volume": self._safe_float(contract.get("total_volume"), 0.0),
                    "open_interest": self._safe_float(contract.get("open_interest"), 0.0),
                    "liquidity_score": round(self._option_liquidity_score(contract), 2),
                    "liquidity_breakout_level": liquidity_plan.get("breakout_level"),
                    "liquidity_breakout_passed": liquidity_plan.get("breakout_passed"),
                    "liquidity_breakout_required": liquidity_plan.get("breakout_required"),
                    "liquidity_atm_volume": liquidity_plan.get("atm_volume"),
                    "liquidity_atm_open_interest": liquidity_plan.get("atm_open_interest"),
                    "liquidity_atm_score": liquidity_plan.get("atm_liquidity_score"),
                    "liquidity_atm_dominates_otm": liquidity_plan.get("atm_liquidity_dominates_otm"),
                    "underlying_target_strike": liquidity_plan.get("target_strike"),
                    "underlying_target_volume": liquidity_plan.get("target_volume"),
                    "underlying_target_open_interest": liquidity_plan.get("target_open_interest"),
                    "underlying_target_liquidity_score": liquidity_plan.get("target_liquidity_score"),
                    "underlying_target_liquidity_metric": liquidity_plan.get("target_liquidity_metric"),
                    "call_wall_strike": liquidity_plan.get("call_wall_strike"),
                    "call_wall_open_interest": liquidity_plan.get("call_wall_open_interest"),
                    "call_wall_volume": liquidity_plan.get("call_wall_volume"),
                    "call_wall_concentration": liquidity_plan.get("call_wall_concentration"),
                    "call_wall_strength": liquidity_plan.get("call_wall_strength"),
                    "call_wall_distance_pct": liquidity_plan.get("call_wall_distance_pct"),
                    "put_wall_strike": liquidity_plan.get("put_wall_strike"),
                    "put_wall_open_interest": liquidity_plan.get("put_wall_open_interest"),
                    "put_wall_volume": liquidity_plan.get("put_wall_volume"),
                    "put_wall_concentration": liquidity_plan.get("put_wall_concentration"),
                    "put_wall_strength": liquidity_plan.get("put_wall_strength"),
                    "put_wall_distance_pct": liquidity_plan.get("put_wall_distance_pct"),
                    "oi_wall_signal": liquidity_plan.get("oi_wall_signal"),
                }
            )

        if not candidates:
            if liquidity_breakout_blocks:
                first_block = liquidity_breakout_blocks[0]
                current_price = self._safe_float(first_block.get("underlying_price"), 0.0)
                breakout_level = self._safe_float(first_block.get("breakout_level"), 0.0)
                if current_price > 0 and breakout_level > 0:
                    return None, (
                        "waiting for ATM liquidity support break: "
                        f"underlying ${current_price:.2f} below ATM ${breakout_level:.2f}; "
                        "ATM OI and volume are both greater than the selected OTM target"
                    )
            spread_note = f", spread filter {self.option_bot_config.get('spreadFilter')}" if self.option_bot_config.get("spreadFilter") else ""
            return None, f"no call contract met delta < {delta_cap:.2f}, valid mid pricing{spread_note}, and expected move >= ${min_expected_move:.2f}"

        selected = sorted(
            candidates,
            key=lambda item: (
                int(item["days_to_expiration"]),
                0 if item.get("underlying_target_strike") and abs(float(item["strike_price"]) - float(item["underlying_target_strike"])) < 0.01 else 1,
                abs(float(item["strike_price"]) - float(item.get("underlying_target_strike") or item["strike_price"])),
                abs(float(item["delta"]) - DEFAULT_OPTION_PREFERRED_DELTA),
                float(item["spread"]),
                -float(item.get("liquidity_score") or 0.0),
            ),
        )[0]
        return selected, ""

    def _option_market_hours_open(self) -> bool:
        session_status = self._session_status(self.client.get_clock())
        return session_status.get("core") == "Open"

    def _option_entry_window_open(self, now_et=None) -> bool:
        """Allow new option entries only from 9:30 AM through 3:44:59 PM ET."""
        if not self._option_market_hours_open():
            return False
        timestamp = pd.Timestamp(now_et) if now_et is not None else pd.Timestamp.now(tz=EASTERN_TZ)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(EASTERN_TZ)
        else:
            timestamp = timestamp.tz_convert(EASTERN_TZ)
        current_time = timestamp.time().replace(tzinfo=None)
        return clock_time(9, 30) <= current_time < clock_time(15, 45)

    def _option_signal_source_bars(self, symbol: str) -> pd.DataFrame:
        target = self._normalize_option_symbol(symbol)
        if not target:
            return pd.DataFrame()
        try:
            frame = self.market_data_client.get_chart_bars(target, timeframe="1Min", days_back=5)
        except Exception:
            return pd.DataFrame()
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame()
        bars = frame.copy()
        timestamps = pd.to_datetime(bars["timestamp"], errors="coerce")
        if getattr(timestamps.dt, "tz", None) is None:
            bars["timestamp"] = timestamps.dt.tz_localize(EASTERN_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            bars["timestamp"] = timestamps.dt.tz_convert(EASTERN_TZ)
        bars = bars.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if bars.empty:
            return pd.DataFrame()
        minutes = (bars["timestamp"].dt.hour * 60) + bars["timestamp"].dt.minute
        # Match the Thinkorswim scan with EXT enabled by keeping the standard
        # US extended session instead of regular-hours-only candles.
        mask = (
            (bars["timestamp"].dt.weekday < 5)
            & (minutes >= (4 * 60))
            & (minutes <= (20 * 60))
        )
        bars = bars.loc[mask].copy()
        if bars.empty:
            return pd.DataFrame()
        return bars.reset_index(drop=True)

    def _aggregate_option_bars(self, bars: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame()
        frame = bars.copy()
        if "timestamp" not in frame.columns:
            return pd.DataFrame()
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
        if frame.empty:
            return pd.DataFrame()
        frequency = f"{int(bucket_minutes)}min"
        return (
            frame.set_index("timestamp")
            .resample(frequency, label="right", closed="right")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

    def _pct_change_vs_bars_back(self, frame: pd.DataFrame, column: str, length: int = DEFAULT_OPTION_SIGNAL_LOOKBACK_BARS) -> float | None:
        bars_back = max(int(length), 1)
        if frame is None or frame.empty or column not in frame.columns or len(frame) <= bars_back:
            return None
        latest = float(frame.iloc[-1][column] or 0.0)
        reference = float(frame.iloc[-(bars_back + 1)][column] or 0.0)
        if reference <= 0:
            return None
        return round((100.0 * ((latest / reference) - 1.0)), 4)

    def _option_signal_checks(self, row: dict) -> dict:
        symbol = self._normalize_option_symbol(row.get("symbol", ""))
        price = float(row.get("last_price") or row.get("entry") or 0.0)
        setup_name = str(row.get("setup_name") or "").strip()
        bars = self._option_signal_source_bars(symbol)
        one_hour = self._aggregate_option_bars(bars, 60)
        four_hour = self._aggregate_option_bars(bars, 240)
        one_hour_price_change_pct = self._pct_change_vs_bars_back(one_hour, "close", DEFAULT_OPTION_SIGNAL_LOOKBACK_BARS)
        four_hour_price_change_pct = self._pct_change_vs_bars_back(four_hour, "close", DEFAULT_OPTION_SIGNAL_LOOKBACK_BARS)
        one_hour_close_change_pct = one_hour_price_change_pct
        four_hour_close_change_pct = four_hour_price_change_pct
        four_hour_volume_change_pct = self._pct_change_vs_bars_back(four_hour, "volume", DEFAULT_OPTION_SIGNAL_LOOKBACK_BARS)

        price_pass = price >= DEFAULT_OPTION_MIN_PRICE
        # Keep 1H/4H changes for the journal and review panels, but do not
        # make them entry gates. The option entry decision is live 5m setup,
        # EMA/VWAP, contract liquidity, and risk driven.
        one_hour_price_pass = None
        one_hour_pass = None
        four_hour_price_pass = None
        four_hour_close_pass = None
        four_hour_volume_pass = True
        ema_trend_pass = bool(row.get("ema_stack"))
        vwap_pass = bool(row.get("above_vwap"))
        four_hour_cloud_pass = bool(row.get("four_hour_cloud_bullish"))
        cloud_alignment_pass = bool(row.get("cloud_alignment_pass")) and four_hour_cloud_pass
        cloud_alignment_action = str(
            row.get("cloud_alignment_action") or "NO TRADE - CLOUDS NOT ALIGNED"
        )
        volume_acceleration_observed = bool(row.get("volume_trend"))
        volume_pass = True
        live_four_hour_volume_pass = True
        trigger_pass = setup_name in OPTION_ALLOWED_SETUPS
        momentum_price_action_pass = trigger_pass
        ema9_retest_observed = bool(row.get("ema9_retest_5m"))
        ema9_retest_pass = True
        all_passed = (
            price_pass
            and ema_trend_pass
            and vwap_pass
            and cloud_alignment_pass
            and momentum_price_action_pass
        )
        rule_passed = all_passed and trigger_pass

        rejection_reasons: list[str] = []
        if not price_pass:
            rejection_reasons.append(f"price below ${DEFAULT_OPTION_MIN_PRICE:.2f}")
        if not ema_trend_pass:
            rejection_reasons.append("EMA trend not stacked: EMA 9 > EMA 21 > EMA 50 required")
        if not vwap_pass:
            rejection_reasons.append("price is not above VWAP")
        if not cloud_alignment_pass:
            rejection_reasons.append(cloud_alignment_action)
        if not momentum_price_action_pass:
            rejection_reasons.append("momentum / price-action confirmation missing")
        if not trigger_pass:
            rejection_reasons.append("setup not in approved option trigger list")

        return {
            "option_rule_price_pass": price_pass,
            "option_rule_ema_trend_pass": ema_trend_pass,
            "option_rule_vwap_pass": vwap_pass,
            "option_rule_four_hour_cloud_pass": four_hour_cloud_pass,
            "option_rule_cloud_alignment_pass": cloud_alignment_pass,
            "option_five_min_cloud_state": row.get("five_min_cloud_state"),
            "option_four_hour_cloud_state": row.get("four_hour_cloud_state"),
            "option_cloud_alignment_action": cloud_alignment_action,
            "option_rule_volume_pass": volume_pass,
            "option_rule_volume_acceleration_observed": volume_acceleration_observed,
            "option_rule_live_four_hour_volume_pass": live_four_hour_volume_pass,
            "option_rule_momentum_price_action_pass": momentum_price_action_pass,
            "option_rule_ema9_retest_pass": ema9_retest_pass,
            "option_rule_ema9_retest_observed": ema9_retest_observed,
            "option_rule_one_hour_close_pass": one_hour_pass,
            "option_rule_one_hour_price_change_pass": one_hour_price_pass,
            "option_rule_four_hour_close_pass": four_hour_close_pass,
            "option_rule_four_hour_price_change_pass": four_hour_price_pass,
            "option_rule_four_hour_volume_pass": four_hour_volume_pass,
            "option_rule_trigger_pass": trigger_pass,
            "option_rule_all_passed": all_passed,
            "option_rule_any_passed": trigger_pass,
            "option_rule_passed": rule_passed,
            "option_rule_trigger_match": setup_name if trigger_pass else "",
            "option_one_hour_close_change_pct": one_hour_close_change_pct,
            "option_one_hour_price_change_pct": one_hour_price_change_pct,
            "option_four_hour_close_change_pct": four_hour_close_change_pct,
            "option_four_hour_price_change_pct": four_hour_price_change_pct,
            "option_four_hour_volume_change_pct": four_hour_volume_change_pct,
            "option_live_four_hour_volume_change_pct": row.get("four_hour_volume_change_pct"),
            "option_live_four_hour_current_volume": row.get("four_hour_current_volume"),
            "option_live_four_hour_volume_2_bars_ago": row.get("four_hour_volume_2_bars_ago"),
            "option_rule_rejection_reason": "; ".join(rejection_reasons),
        }

    def _apply_option_entry_logic(self, candidate_frame: pd.DataFrame) -> pd.DataFrame:
        if candidate_frame is None or candidate_frame.empty:
            return pd.DataFrame()
        enriched_rows: list[dict] = []
        for row in candidate_frame.to_dict("records"):
            option_checks = self._option_signal_checks(row)
            merged = dict(row)
            stock_allowed = bool(row.get("allowed"))
            stock_rejection_reason = str(row.get("rejection_reason") or "").strip()
            merged.update(option_checks)
            merged["stock_allowed"] = stock_allowed
            merged["stock_rejection_reason"] = stock_rejection_reason
            # Option bot ignores stock/AI score approval. Entries are controlled by
            # option-specific signal rules plus contract, spread, delta, and risk checks.
            merged["allowed"] = bool(option_checks["option_rule_passed"])
            merged["rejection_reason"] = "" if merged["allowed"] else (
                option_checks["option_rule_rejection_reason"] or "Option entry logic rejected the signal"
            )
            enriched_rows.append(merged)
        return pd.DataFrame(enriched_rows)

    def _option_candidate_symbol_set(self) -> set[str]:
        if self.option_candidate_results is None or self.option_candidate_results.empty or "symbol" not in self.option_candidate_results.columns:
            return set()
        return {
            self._normalize_option_symbol(symbol)
            for symbol in self.option_candidate_results["symbol"].tolist()
            if self._normalize_option_symbol(symbol)
        }

    def _option_no_candidate_results(self) -> list[dict]:
        if not self.option_scan_timestamp:
            return []
        candidate_symbols = self._option_candidate_symbol_set()
        rows: list[dict] = []
        for symbol in self._active_option_watchlist():
            normalized = self._normalize_option_symbol(symbol)
            if not normalized or normalized in candidate_symbols:
                continue
            rows.append(
                {
                    "symbol": normalized,
                    "stage": "Scanner",
                    "trigger": "No trigger yet",
                    "reason": "No current option setup row from the live 5-minute EMA/VWAP scan.",
                    "lastScan": self.option_scan_timestamp,
                }
            )
        return rows

    def _option_scan_coverage_payload(self) -> dict:
        active_watchlist = self._active_option_watchlist()
        candidate_count = 0
        qualified_count = 0
        if self.option_candidate_results is not None and not self.option_candidate_results.empty:
            candidate_count = len(self.option_candidate_results)
            if "allowed" in self.option_candidate_results.columns:
                qualified_count = int(self.option_candidate_results["allowed"].sum())
            else:
                qualified_count = candidate_count
        no_candidate_rows = self._option_no_candidate_results()
        return {
            "lastScan": self.option_scan_timestamp,
            "watchlistLabel": self._option_watchlist_source_label(),
            "watchlistCount": len(active_watchlist),
            "candidateCount": candidate_count,
            "qualifiedCount": qualified_count,
            "entryRuleBlockedCount": max(candidate_count - qualified_count, 0),
            "plannerBlockedCount": len(self.option_plan_blocks or []),
            "noCandidateCount": len(no_candidate_rows),
            "noCandidateSymbols": [row["symbol"] for row in no_candidate_rows[:50]],
        }

    def _option_supervisor_context(
        self,
        option_candidates: pd.DataFrame | None = None,
        plan_result: dict | None = None,
        manage_result: dict | None = None,
    ) -> dict:
        candidates = option_candidates if option_candidates is not None else self.option_candidate_results
        trade_history = pd.DataFrame()
        catalysts = pd.DataFrame()
        try:
            trade_history = self._enrich_option_trade_history(
                self.repository.get_option_trade_history(
                    limit=100,
                    profile_ids=settings.option_account_profile_ids("paper"),
                    broker_only=True,
                )
            )
            if not trade_history.empty and "account_profile_id" in trade_history.columns:
                trade_history = trade_history[
                    trade_history["account_profile_id"].fillna("").astype(str).str.lower().isin(
                        settings.option_account_profile_ids("paper")
                    )
                ]
        except Exception:
            trade_history = pd.DataFrame()
        if hasattr(self.repository, "get_recent_catalysts"):
            try:
                catalysts = self.repository.get_recent_catalysts(limit=25)
            except Exception:
                catalysts = pd.DataFrame()

        return {
            "bot": {
                "state": self.option_bot_state,
                "message": self.option_bot_message,
                "actionMessage": self.action_message,
            },
            "botConfig": dict(self.option_bot_config),
            "riskConfig": dict(self.option_risk_settings),
            "scanCoverage": self._option_scan_coverage_payload(),
            "candidates": _frame_records(candidates),
            "planBlocks": _serialize_value(self.option_plan_blocks),
            "noCandidateRows": _serialize_value(self._option_no_candidate_results()),
            "planResult": _serialize_value(plan_result or {}),
            "manageResult": _serialize_value(manage_result or {}),
            "tradeHistory": _frame_records(trade_history),
            "positions": [],
            "catalysts": _frame_records(catalysts),
            "rules": {
                "executionAuthority": "rules_engine_only",
                "llmCanPlaceOrders": False,
                "minPrice": DEFAULT_OPTION_MIN_PRICE,
                "defaultDeltaCap": DEFAULT_OPTION_DELTA_CAP,
                "defaultExpectedMove": DEFAULT_OPTION_MIN_EXPECTED_MOVE,
                "oneHourPriceChange": "informational_only",
                "fourHourPriceChange": "informational_only",
                "livePriceChangeUsesCurrentCandle": True,
                "allowedSetups": sorted(OPTION_ALLOWED_SETUPS),
            },
        }

    def _refresh_option_supervisor_report(
        self,
        option_candidates: pd.DataFrame | None = None,
        plan_result: dict | None = None,
        manage_result: dict | None = None,
    ) -> dict:
        supervisor = getattr(self, "option_supervisor", None)
        if supervisor is None:
            supervisor = OptionLLMSupervisor(enabled=False, model=settings.ai.option_llm_supervisor_model)
            self.option_supervisor = supervisor
        try:
            context = self._option_supervisor_context(option_candidates, plan_result, manage_result)
            report = supervisor.review(context)
        except Exception as exc:
            report = self._empty_option_supervisor_report()
            report["status"] = "Supervisor Error"
            report["summary"] = f"Option supervisor failed safely: {exc}"
            report["llm"] = {
                "mode": "rules_fallback",
                "model": settings.ai.option_llm_supervisor_model,
                "latencyMs": 0,
                "note": str(exc),
            }
        self.option_supervisor_report = report
        try:
            self.repository.log_bot_event(
                "option_llm_supervisor",
                str(report.get("summary") or "Option LLM supervisor reviewed latest scan."),
                json.dumps(report, default=str),
            )
        except Exception:
            pass
        return report

    def _schedule_option_supervisor_report(
        self,
        option_candidates: pd.DataFrame | None = None,
        plan_result: dict | None = None,
        manage_result: dict | None = None,
    ) -> bool:
        refresh_lock = getattr(self, "option_supervisor_refresh_lock", None)
        if refresh_lock is None:
            refresh_lock = threading.Lock()
            self.option_supervisor_refresh_lock = refresh_lock
        if not refresh_lock.acquire(blocking=False):
            return False
        candidates_snapshot = option_candidates.copy(deep=True) if isinstance(option_candidates, pd.DataFrame) else option_candidates
        plan_snapshot = dict(plan_result or {})
        manage_snapshot = dict(manage_result or {})

        def runner() -> None:
            try:
                self._refresh_option_supervisor_report(candidates_snapshot, plan_snapshot, manage_snapshot)
            finally:
                refresh_lock.release()

        try:
            refresh_thread = threading.Thread(
                target=runner,
                name="option-information-supervisor",
                daemon=True,
            )
            self.option_supervisor_refresh_thread = refresh_thread
            refresh_thread.start()
        except Exception as exc:
            refresh_lock.release()
            try:
                self.repository.log_bot_event(
                    "option_llm_supervisor_error",
                    f"Informational option review could not start: {exc}",
                )
            except Exception:
                pass
            return False
        return True

    def _option_percent_setting(self, key: str, default: float) -> float:
        parsed = self._parse_numeric_guardrail(self.option_risk_settings.get(key), default)
        if parsed is None or parsed <= 0:
            return default
        return float(parsed)

    def _option_contract_quantity(self, entry_price: float) -> int:
        if entry_price <= 0:
            return 1
        configured_contracts = self._parse_numeric_guardrail(self.option_risk_settings.get("contractQuantity"), None)
        if configured_contracts is not None and configured_contracts > 0:
            return max(int(configured_contracts), 1)
        trade_amount = self._parse_numeric_guardrail(self.option_risk_settings.get("tradeAmount"), None)
        if trade_amount is None or trade_amount <= 0:
            return 1
        contract_cost = entry_price * OPTION_CONTRACT_MULTIPLIER
        if contract_cost <= 0:
            return 1
        return max(int(trade_amount // contract_cost), 1)

    def _option_target_1_contracts(self, quantity: int) -> int:
        if quantity <= 1:
            return 1
        raw = str(self.option_risk_settings.get("firstProfitTargetCons") or "").strip().lower()
        configured = self._parse_numeric_guardrail(raw, None)
        if configured is None or configured <= 0:
            return max(quantity - 1, 1)
        if "%" in raw:
            return min(max(int(quantity * (configured / 100.0)), 1), quantity)
        return min(max(int(configured), 1), quantity)

    def _option_runner_lock_step_percent(self) -> float:
        configured = self._parse_numeric_guardrail(
            self.option_risk_settings.get("runnerLockStepPercent"),
            DEFAULT_OPTION_RUNNER_LOCK_STEP_PCT,
        )
        if configured is None or configured <= 0:
            return DEFAULT_OPTION_RUNNER_LOCK_STEP_PCT
        return float(configured)

    def _option_stop_loss_config(self, entry_price: float) -> dict:
        raw = str(self.option_risk_settings.get("stopLossPercent") or "").strip()
        configured = self._parse_numeric_guardrail(raw, None) if raw else None
        if configured is not None and configured > 0:
            stop_price = round(max(entry_price * (1 - (float(configured) / 100.0)), 0.01), 4)
            return {
                "mode": "manual_percent",
                "percent": float(configured),
                "price": stop_price,
                "label": f"{float(configured):g}% premium stop",
            }
        return {
            "mode": "ema20_candle",
            "percent": None,
            "price": None,
            "label": "5-minute candle close below EMA20",
        }

    def _option_initial_trade_plan(self, entry_price: float, quantity: int, selected_contract: dict, row: dict) -> dict:
        stop_config = self._option_stop_loss_config(entry_price)
        target_pct = self._option_percent_setting("firstProfitTargetPercent", DEFAULT_OPTION_FIRST_TARGET_PCT)
        runner_lock_step_pct = self._option_runner_lock_step_percent()
        stop_price = stop_config["price"]
        target_price = round(entry_price * (1 + (target_pct / 100.0)), 4)
        underlying_target_strike = self._safe_float(selected_contract.get("underlying_target_strike"), 0.0)
        if underlying_target_strike > 0 and quantity > 1:
            target_contracts = min(max(int(quantity * (DEFAULT_OPTION_LIQUIDITY_TARGET_SELL_PCT / 100.0)), 1), quantity)
        else:
            target_contracts = self._option_target_1_contracts(quantity)
        return {
            "automation": "option_paper_engine_v1",
            "option_lifecycle": "position_open",
            "entry_mid": round(entry_price, 4),
            "current_mid": round(entry_price, 4),
            "stop_loss_mode": stop_config["mode"],
            "stop_loss_label": stop_config["label"],
            "stop_loss_percent": stop_config["percent"],
            "first_profit_target_percent": target_pct,
            "runner_lock_step_percent": runner_lock_step_pct,
            "take_profit_1": target_price,
            "contracts_to_sell_at_target_1": target_contracts,
            "underlying_target_1_strike": underlying_target_strike if underlying_target_strike > 0 else None,
            "underlying_target_1_sell_percent": DEFAULT_OPTION_LIQUIDITY_TARGET_SELL_PCT if underlying_target_strike > 0 else None,
            "liquidity_breakout_level": selected_contract.get("liquidity_breakout_level"),
            "liquidity_breakout_passed": selected_contract.get("liquidity_breakout_passed"),
            "liquidity_breakout_required": selected_contract.get("liquidity_breakout_required"),
            "liquidity_atm_volume": selected_contract.get("liquidity_atm_volume"),
            "liquidity_atm_open_interest": selected_contract.get("liquidity_atm_open_interest"),
            "liquidity_atm_score": selected_contract.get("liquidity_atm_score"),
            "liquidity_atm_dominates_otm": selected_contract.get("liquidity_atm_dominates_otm"),
            "underlying_target_volume": selected_contract.get("underlying_target_volume"),
            "underlying_target_open_interest": selected_contract.get("underlying_target_open_interest"),
            "underlying_target_liquidity_score": selected_contract.get("underlying_target_liquidity_score"),
            "underlying_target_liquidity_metric": selected_contract.get("underlying_target_liquidity_metric"),
            "remaining_quantity": quantity,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "marked_pnl": 0.0,
            "partial_exit_taken": False,
            "partial_exit_price": None,
            "partial_exit_qty": None,
            "runner_stop": stop_price,
            "runner_stop_locked_pct": round(((stop_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 and stop_price else None,
            "runner_exit_price": None,
            "runner_exit_reason": None,
            "selected_option_symbol": selected_contract["symbol"],
            "selected_option_mid": selected_contract["mid"],
            "selected_option_bid": selected_contract["bid"],
            "selected_option_ask": selected_contract["ask"],
            "selected_option_delta": selected_contract["delta"],
            "selected_option_expected_move": selected_contract["expected_move"],
            "selected_option_expiry": selected_contract["expiry_date"],
            "selected_option_strike": selected_contract["strike_price"],
            "selected_option_spread": selected_contract.get("spread"),
            "selected_option_spread_percent": selected_contract.get("spread_percent"),
            "selected_option_volume": selected_contract.get("total_volume"),
            "selected_option_open_interest": selected_contract.get("open_interest"),
            "selected_option_liquidity_score": selected_contract.get("liquidity_score"),
            "strategy_family": row.get("strategy_family"),
            "setup_name": row.get("setup_name"),
        }

    def _option_ema20_underlying_break(self, underlying_symbol: str) -> dict | None:
        symbol = self._normalize_option_symbol(underlying_symbol)
        if not symbol or not hasattr(self.market_data_client, "get_chart_bars"):
            return None
        try:
            frame = self.market_data_client.get_chart_bars(symbol, timeframe="5Min", days_back=3)
        except Exception:
            return None
        if frame is None or frame.empty or len(frame) < 20:
            return None
        bars = frame.copy().sort_values("timestamp").reset_index(drop=True)
        if "close" not in bars.columns:
            return None
        bars["ema_20"] = ema(pd.to_numeric(bars["close"], errors="coerce"), 20)
        latest = bars.iloc[-1]
        latest_close = self._safe_float(latest.get("close"), 0.0)
        latest_ema20 = self._safe_float(latest.get("ema_20"), 0.0)
        if latest_close <= 0 or latest_ema20 <= 0:
            return None
        if latest_close < latest_ema20:
            return {
                "reason": "option_underlying_5m_ema20_break",
                "underlying": symbol,
                "close": round(latest_close, 4),
                "ema20": round(latest_ema20, 4),
                "timestamp": _serialize_value(latest.get("timestamp")),
            }
        return None

    def _option_underlying_last_price(self, underlying_symbol: str) -> float:
        symbol = self._normalize_option_symbol(underlying_symbol)
        if not symbol or not hasattr(self.market_data_client, "get_chart_bars"):
            return 0.0
        try:
            frame = self.market_data_client.get_chart_bars(symbol, timeframe="1Min", days_back=1)
        except Exception:
            return 0.0
        if frame is None or frame.empty or "close" not in frame.columns:
            return 0.0
        bars = frame.copy().sort_values("timestamp") if "timestamp" in frame.columns else frame.copy()
        return self._safe_float(bars.iloc[-1].get("close"), 0.0)

    def _option_trade_plan_state(self, row: dict) -> dict:
        raw_analysis = row.get("analysis_json")
        plan = self._safe_json_loads(raw_analysis)
        if isinstance(plan, dict):
            return plan
        return {}

    def _option_quote_for_trade(self, row: dict) -> dict | None:
        if settings.market_data_provider != "schwab" or not hasattr(self.market_data_client, "get_option_chain"):
            return None
        underlying = self._normalize_option_symbol(row.get("underlying_symbol", ""))
        option_symbol = str(row.get("option_symbol") or "").strip()
        if not underlying or not option_symbol:
            return None
        try:
            chain_payload = self.market_data_client.get_option_chain(underlying, contract_type="CALL", strike_count=80)
        except Exception:
            return None
        normalized_option_symbol = self.option_client.normalize_option_symbol(option_symbol)
        for contract in self._option_chain_contracts(chain_payload, "CALL"):
            contract_symbol = self.option_client.normalize_option_symbol(
                str(contract.get("symbol") or contract.get("description") or "").strip()
            )
            if contract_symbol != normalized_option_symbol:
                continue
            bid = float(contract.get("bid") or 0.0)
            ask = float(contract.get("ask") or 0.0)
            mid = self._contract_mid_price(contract)
            if mid <= 0:
                return None
            return {
                "symbol": contract_symbol,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "delta": float(contract.get("delta") or 0.0),
                "strike_price": float(contract.get("strike_price") or 0.0),
                "expiry_date": str(contract.get("expiry_date") or ""),
            }
        return None

    @staticmethod
    def _approved_oi_strategy_frame(
        strategy_frame: pd.DataFrame,
        relax_price_volume_gates: bool = False,
        require_mtf_signal: bool = True,
    ) -> pd.DataFrame:
        if strategy_frame is None or strategy_frame.empty:
            return strategy_frame
        last_price = pd.to_numeric(
            strategy_frame.get("last_price", pd.Series(0.0, index=strategy_frame.index)),
            errors="coerce",
        ).fillna(0.0)
        one_hour_close_pass = strategy_frame.get(
            "one_hour_price_change_pass",
            strategy_frame.get("one_hour_close_pass", pd.Series(False, index=strategy_frame.index)),
        ).fillna(False)
        four_hour_volume_pass = strategy_frame.get(
            "four_hour_volume_pass",
            pd.Series(False, index=strategy_frame.index),
        ).fillna(False)
        ema_stack = strategy_frame.get("ema_stack", pd.Series(False, index=strategy_frame.index)).fillna(False)
        above_vwap = strategy_frame.get("above_vwap", pd.Series(False, index=strategy_frame.index)).fillna(False)
        cloud_alignment = strategy_frame.get(
            "cloud_alignment_pass", pd.Series(False, index=strategy_frame.index)
        ).fillna(False)
        rvol_any = strategy_frame.get(
            "tos_rvol_any_pass", pd.Series(False, index=strategy_frame.index)
        ).fillna(False) | strategy_frame.get(
            "tos_rvol_5m_early_alert", pd.Series(False, index=strategy_frame.index)
        ).fillna(False)
        fast_momentum = pd.to_numeric(
            strategy_frame.get("fast_momentum_score", pd.Series(0, index=strategy_frame.index)),
            errors="coerce",
        ).fillna(0).ge(2)
        price_action = strategy_frame.get(
            "price_action_pass", pd.Series(False, index=strategy_frame.index)
        ).fillna(False)
        mtf_bullish_signal = strategy_frame.get(
            "mtf_bullish_signal_pass",
            pd.Series(False, index=strategy_frame.index),
        ).fillna(False)
        approved_mask = (
            strategy_frame["setup_name"].isin(OPTION_ALLOWED_SETUPS)
            & last_price.ge(float(settings.scanner.min_price))
            & ema_stack.astype(bool)
            & above_vwap.astype(bool)
            & cloud_alignment.astype(bool)
            & rvol_any.astype(bool)
            & fast_momentum
            & price_action.astype(bool)
        )
        if require_mtf_signal:
            approved_mask = approved_mask & mtf_bullish_signal
        if not relax_price_volume_gates:
            approved_mask = approved_mask & one_hour_close_pass.astype(bool) & four_hour_volume_pass.astype(bool)
        return strategy_frame[approved_mask]

    def scan_option_chain_liquidity(
        self,
        symbols: list[str] | None = None,
        min_delta: float = 0.20,
        max_per_symbol: int = 100,
        min_expected_move: float = 0.0,
        allow_zero_dte_after_hours: bool = False,
        min_underlying_price: float = 0.0,
        max_days_to_expiration: int | None = None,
        min_one_hour_close_change_pct: float | None = None,
        rvol_confirmation_threshold: float | None = None,
        relax_price_volume_gates: bool = False,
        require_mtf_gate: bool = True,
    ) -> dict:
        # The OI scanner must always read option chains from the user's
        # authenticated Schwab/TOS connection.  It does not inherit the
        # app-wide market-data provider (which can be Alpaca for stock data).
        schwab_market_client = SchwabClient()
        if not schwab_market_client.configured:
            return {
                "source": "Schwab/TOS option chain",
                "minDelta": min_delta,
                "minExpectedMove": min_expected_move,
                "rows": [],
                "errors": [{"symbol": "ALL", "error": "Schwab/TOS option chain client is not available."}],
            }
        underlyings = self._normalize_option_watchlist(symbols or self._mag7_oi_underlyings())
        min_delta = max(float(min_delta or 0.20), 0.0)
        max_per_symbol = max(int(max_per_symbol or 5), 1)
        min_expected_move = max(float(min_expected_move or 0.0), 0.0)
        min_underlying_price = max(float(min_underlying_price or 0.0), 0.0)
        max_days_to_expiration = None if max_days_to_expiration is None else max(int(max_days_to_expiration), 0)
        effective_rvol_threshold = float(
            self.scanner.settings.tos_rvol_num_dev
            if rvol_confirmation_threshold is None
            else rvol_confirmation_threshold
        )
        rows: list[dict] = []
        errors: list[dict] = []
        quote_map: dict[str, dict] = {}
        get_quotes = getattr(schwab_market_client, "get_quotes", None)
        if callable(get_quotes):
            try:
                quote_map = get_quotes(underlyings) or {}
            except Exception as exc:
                errors.append({"symbol": "QUOTES", "error": str(exc)})

        # Quotes are cheap compared with rebuilding multi-timeframe candles.
        # Only prefilter Last >= min here; the live 1H/4H conditions are enforced
        # from the candle frame after the full strategy calculation.
        strategy_underlyings: list[str] = []
        for underlying in underlyings:
            quote = quote_map.get(underlying)
            if not isinstance(quote, dict) or not quote:
                strategy_underlyings.append(underlying)
                continue
            quote_price = self._safe_float(quote.get("last_price"), 0.0)
            if quote_price >= min_underlying_price:
                strategy_underlyings.append(underlying)

        setup_map: dict[str, dict] = {}
        strategy_frame = self.scanner.run(
            symbols=strategy_underlyings,
            max_results=0,
            ignore_one_hour_price_change=True,
            ignore_four_hour_price_change=True,
            ignore_four_hour_volume=True,
            ignore_ema9_retest=True,
            rvol_confirmation_threshold=effective_rvol_threshold,
            allow_mtf_signal_setup=True,
            require_rvol_confirmation=False,
        )
        if strategy_frame is not None and not strategy_frame.empty:
            approved_strategy_frame = self._approved_oi_strategy_frame(
                strategy_frame,
                relax_price_volume_gates=relax_price_volume_gates,
                require_mtf_signal=require_mtf_gate,
            )
            for row in approved_strategy_frame.to_dict("records"):
                symbol = self._normalize_option_symbol(row.get("symbol", ""))
                if not symbol:
                    continue
                setup_map[symbol] = row
        anchor_time = datetime.now().astimezone()
        session_status = self._session_status(self.client.get_clock())
        allow_zero_dte = bool(
            allow_zero_dte_after_hours
            or str(session_status.get("currentSession") or "").strip() == "Core"
        )
        eligible_underlyings = [underlying for underlying in strategy_underlyings if setup_map.get(underlying)]

        chain_payloads: dict[str, dict] = {}
        if eligible_underlyings:
            # Two requests at a time is enough for Mag7 and avoids a burst at
            # the broker edge while a manual or background scan is running.
            max_workers = min(max(len(eligible_underlyings), 1), 2)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        schwab_market_client.get_option_chain,
                        underlying,
                        contract_type="ALL",
                        strike_count=80,
                    ): underlying
                    for underlying in eligible_underlyings
                }
                for future in as_completed(future_map):
                    underlying = future_map[future]
                    try:
                        chain_payload = future.result()
                    except Exception as exc:
                        errors.append({"symbol": underlying, "error": str(exc)})
                        continue
                    if not chain_payload:
                        errors.append({"symbol": underlying, "error": "Option chain payload was empty."})
                        continue
                    chain_payloads[underlying] = chain_payload

        for underlying in eligible_underlyings:
            setup_row = setup_map.get(underlying)
            chain_payload = chain_payloads.get(underlying)
            if not setup_row or not chain_payload:
                continue
            quote = quote_map.get(underlying) or {}
            underlying_price = self._option_underlying_price_from_chain(chain_payload)
            if underlying_price <= 0:
                underlying_price = self._safe_float(quote.get("last_price"), 0.0)
            live_change_raw = quote.get("change_pct")
            if live_change_raw is None:
                live_change_raw = setup_row.get("session_change_pct")
            live_change_pct = self._safe_float(live_change_raw, 0.0)
            if underlying_price < min_underlying_price:
                continue
            expiry_cutoff = self._oi_symbol_expiry_cutoff(chain_payload, anchor_time)
            expected_move_by_expiry: dict[str, float | None] = {}
            liquidity_plan_by_expiry: dict[str, dict] = {}
            symbol_rows: list[dict] = []
            for contract in self._option_chain_contracts(chain_payload, "CALL"):
                strike = self._safe_float(contract.get("strike_price"), 0.0)
                delta = self._safe_float(contract.get("delta"), 0.0)
                expiry = str(contract.get("expiry_date") or "")
                days_to_expiration = int(contract.get("daysToExpiration") or 0)
                expiry_timestamp = self._expiry_timestamp(expiry)
                if underlying_price <= 0 or strike <= underlying_price or delta < min_delta:
                    continue
                # 0DTE is useful during core market hours, but should stay out
                # after-hours when same-day contracts are expired/noisy.
                if days_to_expiration <= 0 and not allow_zero_dte:
                    continue
                if expiry_cutoff is not None and expiry_timestamp is not None and expiry_timestamp.date() > expiry_cutoff:
                    continue
                if max_days_to_expiration is not None and days_to_expiration > max_days_to_expiration:
                    continue
                if expiry not in expected_move_by_expiry:
                    expected_move_by_expiry[expiry] = self._expected_move_for_expiry(chain_payload, expiry)
                expected_move = expected_move_by_expiry.get(expiry)
                if expected_move is None or expected_move < min_expected_move:
                    continue
                bid = self._safe_float(contract.get("bid"), 0.0)
                ask = self._safe_float(contract.get("ask"), 0.0)
                mid = self._contract_mid_price(contract)
                volume = self._safe_float(contract.get("total_volume"), 0.0)
                open_interest = self._safe_float(contract.get("open_interest"), 0.0)
                if volume <= 0 and open_interest <= 0:
                    continue
                contract_symbol = str(contract.get("symbol") or contract.get("description") or "").strip()
                if expiry and expiry not in liquidity_plan_by_expiry:
                    liquidity_plan_by_expiry[expiry] = self._option_liquidity_strike_plan(chain_payload, expiry)
                liquidity_plan = liquidity_plan_by_expiry.get(expiry) or {}
                atm_strike = self._safe_float(liquidity_plan.get("atm_strike"), 0.0)
                atm_volume = self._safe_float(liquidity_plan.get("atm_volume"), 0.0)
                atm_open_interest = self._safe_float(liquidity_plan.get("atm_open_interest"), 0.0)
                otm_score = volume + open_interest
                atm_score = atm_volume + atm_open_interest
                liquidity_winner = "ATM > OTM" if atm_score >= otm_score else "OTM > ATM"
                flow_type = "Big real-time volume only" if volume > open_interest else "Big OI only"
                setup_type = (
                    "ATM momentum" if flow_type == "Big real-time volume only" and liquidity_winner == "ATM > OTM"
                    else "OTM momentum" if flow_type == "Big real-time volume only"
                    else "ATM positioning" if liquidity_winner == "ATM > OTM"
                    else "OTM positioning"
                )
                symbol_rows.append(
                    {
                        "underlying": underlying,
                        "underlying_price": round(underlying_price, 4),
                        "contract": self.option_client.normalize_option_symbol(contract_symbol) if contract_symbol else "",
                        "source_contract": contract_symbol,
                        "expiry": expiry,
                        "days_to_expiration": days_to_expiration,
                        "strike": strike,
                        "delta": round(delta, 4),
                        "bid": bid,
                        "ask": ask,
                        "mid": mid,
                        "expected_move": expected_move,
                        "volume": volume,
                        "open_interest": open_interest,
                        "change_pct": round(live_change_pct, 2),
                        # Kept for older stored rows and API consumers. This now
                        # carries live quote change versus prior close, not a 1H move.
                        "one_hour_close_change_pct": round(live_change_pct, 2),
                        "price_pass": underlying_price >= min_underlying_price,
                        "change_pass": live_change_pct >= 1.0,
                        "one_hour_close_pass": live_change_pct >= 1.0,
                        "stock_setup_name": setup_row.get("setup_name") or "TOS Bull Momo",
                        "stock_trigger_source": setup_row.get("trigger_source"),
                        "stock_above_vwap": bool(setup_row.get("above_vwap")),
                        "stock_ema_stack": bool(setup_row.get("ema_stack")),
                        "stock_five_min_cloud_state": setup_row.get("five_min_cloud_state"),
                        "stock_five_min_ema_9": setup_row.get("five_min_ema_9"),
                        "stock_five_min_ema_21": setup_row.get("five_min_ema_21"),
                        "stock_five_min_ema_50": setup_row.get("five_min_ema_50"),
                        "stock_four_hour_cloud_state": setup_row.get("four_hour_cloud_state"),
                        "stock_four_hour_cloud_bullish": bool(setup_row.get("four_hour_cloud_bullish")),
                        "stock_four_hour_ema_9": setup_row.get("four_hour_ema_9"),
                        "stock_four_hour_ema_21": setup_row.get("four_hour_ema_21"),
                        "stock_four_hour_ema_50": setup_row.get("four_hour_ema_50"),
                        "stock_cloud_alignment_pass": bool(setup_row.get("cloud_alignment_pass")),
                        "stock_cloud_alignment_action": setup_row.get("cloud_alignment_action"),
                        "stock_volume_trend": bool(setup_row.get("volume_trend")),
                        "stock_fast_momentum_score": int(setup_row.get("fast_momentum_score") or 0),
                        "stock_fast_momentum_status": setup_row.get("fast_momentum_status") or "UNAVAILABLE",
                        "stock_projected_5m_volume": setup_row.get("projected_5m_volume"),
                        "stock_projected_5m_volume_ratio": setup_row.get("projected_5m_volume_ratio"),
                        "stock_buying_pressure_pct": setup_row.get("buying_pressure_pct"),
                        "stock_previous_5m_high": setup_row.get("previous_5m_high"),
                        "stock_fast_volume_pass": bool(setup_row.get("fast_volume_pass")),
                        "stock_fast_buying_pressure_pass": bool(setup_row.get("fast_buying_pressure_pass")),
                        "stock_fast_previous_high_break_pass": bool(setup_row.get("fast_previous_high_break_pass")),
                        "stock_ema9_retest_observed": bool(setup_row.get("ema9_retest_5m")),
                        "stock_mtf_bullish_signal_pass": bool(setup_row.get("mtf_bullish_signal_pass")),
                        "stock_mtf_bullish_signal_labels": setup_row.get("mtf_bullish_signal_labels"),
                        "stock_mtf_bullish_signal_families": setup_row.get("mtf_bullish_signal_families"),
                        "stock_mtf_bullish_signal_timeframes": setup_row.get("mtf_bullish_signal_timeframes"),
                        "stock_mtf_bullish_signal_both_2h_4h": bool(setup_row.get("mtf_bullish_signal_both_2h_4h")),
                        "stock_tos_rvol_timeframes": setup_row.get("tos_rvol_timeframes"),
                        "stock_tos_rvol_5m": setup_row.get("tos_rvol_5m"),
                        "stock_tos_rvol_15m": setup_row.get("tos_rvol_15m"),
                        "stock_tos_rvol_30m": setup_row.get("tos_rvol_30m"),
                        "stock_tos_rvol_1h": setup_row.get("tos_rvol_1h"),
                        "stock_tos_rvol_2h": setup_row.get("tos_rvol_2h"),
                        "stock_tos_rvol_4h": setup_row.get("tos_rvol_4h"),
                        "stock_tos_rvol_1d": setup_row.get("tos_rvol_1d"),
                        "stock_tos_rvol_any_pass": bool(setup_row.get("tos_rvol_any_pass")),
                        "stock_tos_rvol_5m_early_alert": bool(setup_row.get("tos_rvol_5m_early_alert")),
                        "stock_rvol_any_timeframe_pass": bool(setup_row.get("rvol_any_timeframe_pass")),
                        "stock_price_action_pass": bool(setup_row.get("price_action_pass")),
                        "stock_gate_label": (
                            "ALL required: candle data; Last >= $3; "
                            + ("one valid live 5m MTF group; " if require_mtf_gate else "live 5m MTF group optional; ")
                            + (
                                ""
                                if relax_price_volume_gates
                                else "4H volume >= 0.5% vs 2 bars ago EXT; 1H close >= 0.3% vs 2 bars ago EXT; "
                            )
                            + "live Change % >= 1.00%; live 5m EMA+VWAP; live 5M+4H bullish cloud; RVOL any timeframe; "
                            "fast momentum >= 2/3; bullish 5m previous-high break. MTF CALL2H/CALL4H require "
                            "yellow+cyan; BOTH requires CALL2H and CALL4H with either/both colors; C2H/C4H accept either/both"
                        ),
                        "atm_strike": atm_strike if atm_strike > 0 else None,
                        "atm_volume": atm_volume,
                        "atm_open_interest": atm_open_interest,
                        "flow_type": flow_type,
                        "liquidity_winner": liquidity_winner,
                        "setup_type": setup_type,
                        "scanner_tag": f"{flow_type} + {liquidity_winner}",
                        "atm_liquidity_dominates_otm": bool(liquidity_plan.get("atm_liquidity_dominates_otm")),
                        "call_wall_strike": liquidity_plan.get("call_wall_strike"),
                        "call_wall_open_interest": liquidity_plan.get("call_wall_open_interest"),
                        "call_wall_volume": liquidity_plan.get("call_wall_volume"),
                        "call_wall_concentration": liquidity_plan.get("call_wall_concentration"),
                        "call_wall_strength": liquidity_plan.get("call_wall_strength"),
                        "call_wall_distance_pct": liquidity_plan.get("call_wall_distance_pct"),
                        "put_wall_strike": liquidity_plan.get("put_wall_strike"),
                        "put_wall_open_interest": liquidity_plan.get("put_wall_open_interest"),
                        "put_wall_volume": liquidity_plan.get("put_wall_volume"),
                        "put_wall_concentration": liquidity_plan.get("put_wall_concentration"),
                        "put_wall_strength": liquidity_plan.get("put_wall_strength"),
                        "put_wall_distance_pct": liquidity_plan.get("put_wall_distance_pct"),
                        "oi_wall_signal": liquidity_plan.get("oi_wall_signal"),
                        "oi_wall_aligned": bool(
                            self._safe_float(liquidity_plan.get("call_wall_strike"), 0.0) > 0
                            and abs(strike - self._safe_float(liquidity_plan.get("call_wall_strike"), 0.0)) < 0.01
                        ),
                        "liquidity_score": max(volume, open_interest),
                        "volume_plus_oi": volume + open_interest,
                        "reason": flow_type,
                    }
                )
            symbol_rows = sorted(
                symbol_rows,
                key=lambda row: (
                    row["days_to_expiration"],
                    0 if row.get("oi_wall_aligned") else 1,
                    -row["liquidity_score"],
                    -row["volume_plus_oi"],
                    row["strike"],
                ),
            )
            rows.extend(symbol_rows[:max_per_symbol])
        rows = sorted(
            rows,
            key=lambda row: (
                row["underlying"],
                row["days_to_expiration"],
                -row["liquidity_score"],
                -row["volume_plus_oi"],
            ),
        )
        return {
            "source": "Schwab/TOS option chain",
            "scannedAt": _serialize_value(datetime.now().astimezone()),
            "symbolsScanned": underlyings,
            "symbolCount": len(underlyings),
            "quotePrefilterCount": len(strategy_underlyings),
            "strategyMatchCount": len(eligible_underlyings),
            "minDelta": min_delta,
            "minExpectedMove": min_expected_move,
            "minUnderlyingPrice": min_underlying_price,
            "minChangePct": 1.0,
            "minTosRvolAnyTimeframe": effective_rvol_threshold,
            "requireMtfGate": bool(require_mtf_gate),
            "allowedSetups": sorted(OPTION_ALLOWED_SETUPS),
            "allowZeroDteAfterHours": allow_zero_dte_after_hours,
            "allowZeroDte": allow_zero_dte,
            "maxDaysToExpiration": max_days_to_expiration,
            "maxPerSymbol": max_per_symbol,
            "rows": rows,
            "errors": errors,
        }

    def _option_broker_snapshot(self) -> dict:
        positions = self.option_client.get_option_positions()
        open_orders = self.option_client.get_option_orders(status=QueryOrderStatus.OPEN, limit=500)
        recent_orders = self.option_client.get_option_orders(
            status=QueryOrderStatus.ALL,
            after=datetime.now(timezone.utc) - timedelta(days=45),
            limit=500,
        )
        position_map = {
            self.option_client.normalize_option_symbol(str(getattr(position, "symbol", ""))): position
            for position in positions
            if str(getattr(position, "symbol", "")).strip()
        }
        order_map = {}
        for order in [*recent_orders, *open_orders]:
            client_order_id = str(getattr(order, "client_order_id", "") or "").strip()
            if client_order_id:
                order_map[client_order_id] = order
        open_sell_symbols = {
            self.option_client.normalize_option_symbol(str(getattr(order, "symbol", "")))
            for order in open_orders
            if str(getattr(order, "side", "") or "").strip().lower().endswith("sell")
        }
        return {
            "positions": positions,
            "position_map": position_map,
            "open_orders": open_orders,
            "order_map": order_map,
            "open_sell_symbols": open_sell_symbols,
        }

    def _option_broker_orders_payload(self, broker_snapshot: dict | None = None) -> list[dict]:
        snapshot = broker_snapshot or {}
        seen: set[str] = set()
        rows: list[dict] = []
        orders = [
            *(snapshot.get("open_orders") or []),
            *((snapshot.get("order_map") or {}).values()),
        ]
        for order in orders:
            order_id = str(getattr(order, "id", "") or getattr(order, "client_order_id", "") or "").strip()
            client_order_id = str(getattr(order, "client_order_id", "") or "").strip()
            dedupe_key = order_id or client_order_id
            if dedupe_key and dedupe_key in seen:
                continue
            if dedupe_key:
                seen.add(dedupe_key)
            symbol = self.option_client.normalize_option_symbol(str(getattr(order, "symbol", "") or ""))
            if not symbol or not self.option_client.is_option_symbol(symbol):
                continue
            qty = self._safe_float(getattr(order, "qty", None), self._safe_float(getattr(order, "filled_qty", None), 0.0))
            filled_qty = self._safe_float(getattr(order, "filled_qty", None), 0.0)
            limit_price = self._safe_float(getattr(order, "limit_price", None), 0.0)
            filled_avg_price = self._safe_float(getattr(order, "filled_avg_price", None), 0.0)
            submitted_at = getattr(order, "submitted_at", None) or getattr(order, "created_at", None)
            rows.append(
                {
                    "id": order_id,
                    "clientOrderId": client_order_id,
                    "symbol": symbol,
                    "underlying": self._occ_underlying(symbol),
                    "side": str(getattr(order, "side", "") or "").split(".")[-1].lower(),
                    "status": str(getattr(order, "status", "") or "").split(".")[-1].lower(),
                    "type": str(getattr(order, "type", "") or getattr(order, "order_type", "") or "").split(".")[-1].lower(),
                    "timeInForce": str(getattr(order, "time_in_force", "") or "").split(".")[-1].lower(),
                    "qty": qty,
                    "filledQty": filled_qty,
                    "limitPrice": limit_price,
                    "filledAvgPrice": filled_avg_price,
                    "submittedAt": submitted_at,
                    "filledAt": getattr(order, "filled_at", None),
                    "canceledAt": getattr(order, "canceled_at", None),
                    "notionalCost": round((filled_avg_price or limit_price) * max(filled_qty or qty, 0.0) * OPTION_CONTRACT_MULTIPLIER, 2),
                }
            )
        return sorted(
            rows,
            key=lambda item: str(item.get("submittedAt") or item.get("filledAt") or ""),
            reverse=True,
        )

    def _sync_option_broker_state(self) -> dict:
        history = self.repository.get_option_trade_history(limit=1000, profile_id=self._option_account_profile_id())
        if history.empty:
            return {
                "positions": [],
                "position_map": {},
                "open_orders": [],
                "order_map": {},
                "open_sell_symbols": set(),
            }

        try:
            snapshot = self._option_broker_snapshot()
        except Exception as exc:
            self.repository.log_bot_event("option_broker_sync_error", f"Option broker sync failed: {exc}")
            return {
                "positions": [],
                "position_map": {},
                "open_orders": [],
                "order_map": {},
                "open_sell_symbols": set(),
            }

        position_map = snapshot["position_map"]
        order_map = snapshot["order_map"]
        open_sell_symbols = snapshot["open_sell_symbols"]
        for row in history.to_dict("records"):
            client_order_id = str(row.get("client_order_id") or "").strip()
            if not client_order_id:
                continue

            option_symbol = self.option_client.normalize_option_symbol(row.get("option_symbol") or "")
            if not option_symbol:
                continue

            plan = self._option_trade_plan_state(row)
            broker_order = order_map.get(client_order_id)
            position = position_map.get(option_symbol)
            current_status = str(row.get("status") or "").strip().lower()
            merged_plan = dict(plan)
            update_fields: dict = {}

            if (
                not broker_order
                and not position
                and not str(row.get("broker_order_id") or "").strip()
                and current_status in OPTION_LOCAL_ONLY_ARCHIVE_STATUSES
            ):
                archive_note = "Archived local-only option record after Alpaca-only sync."
                existing_note = str(row.get("notes") or "").strip()
                merged_plan.update(
                    {
                        "broker_status": "archived_local_only",
                        "remaining_quantity": 0,
                        "unrealized_pnl": 0.0,
                        "option_lifecycle": "archived_local_only",
                    }
                )
                update_fields["status"] = "archived_local_only"
                update_fields["closed_at"] = row.get("closed_at") or datetime.now(timezone.utc).isoformat()
                update_fields["notes"] = existing_note if archive_note in existing_note else (
                    f"{existing_note} | {archive_note}" if existing_note else archive_note
                )
                analysis_json = json.dumps(merged_plan, default=str) if merged_plan else None
                if analysis_json:
                    update_fields["analysis_json"] = analysis_json
                self.repository.update_option_trade(
                    client_order_id=client_order_id,
                    **update_fields,
                )
                continue

            if broker_order:
                broker_status = str(getattr(broker_order, "status", "") or "").strip().lower()
                merged_plan["broker_status"] = broker_status
                merged_plan["broker_order_id"] = str(getattr(broker_order, "id", "") or "")
                merged_plan["alpaca_submitted_at"] = _serialize_value(getattr(broker_order, "submitted_at", None))
                update_fields["broker_order_id"] = merged_plan["broker_order_id"]
                if self._safe_float(getattr(broker_order, "filled_avg_price", None), 0.0) > 0:
                    filled_entry = self._safe_float(getattr(broker_order, "filled_avg_price", None), 0.0)
                    merged_plan["entry_mid"] = round(filled_entry, 4)
                    merged_plan["selected_option_mid"] = round(filled_entry, 4)
                    update_fields["entry_price"] = filled_entry
                if not position and broker_status in OPTION_BROKER_REJECTED_STATUSES:
                    update_fields["status"] = broker_status
                    update_fields["notes"] = str(row.get("notes") or broker_status)
                elif not position and current_status not in OPTION_EXIT_PENDING_STATUSES and broker_status in OPTION_BROKER_OPEN_ORDER_STATUSES:
                    update_fields["status"] = broker_status

            if position:
                current_mid = self._safe_float(getattr(position, "current_price", None), 0.0)
                position_qty = abs(self._safe_float(getattr(position, "qty", None), row.get("quantity") or 0.0))
                journal_quantity = abs(self._safe_float(row.get("quantity"), 0.0))
                row_open_quantity = min(position_qty, journal_quantity) if journal_quantity > 0 else position_qty
                position_entry = self._safe_float(
                    getattr(position, "avg_entry_price", None),
                    update_fields.get("entry_price") or row.get("entry_price") or merged_plan.get("entry_mid") or 0.0,
                )
                realized_pnl = self._safe_float(merged_plan.get("realized_pnl"), 0.0)
                unrealized_pnl = self._safe_float(
                    getattr(position, "unrealized_pl", None),
                    (current_mid - position_entry) * row_open_quantity * OPTION_CONTRACT_MULTIPLIER,
                )
                if journal_quantity > 0 and position_qty > journal_quantity:
                    unrealized_pnl = (current_mid - position_entry) * row_open_quantity * OPTION_CONTRACT_MULTIPLIER
                marked_pnl = round(realized_pnl + unrealized_pnl, 2)
                merged_plan.update(
                    {
                        "entry_mid": round(position_entry, 4) if position_entry > 0 else merged_plan.get("entry_mid"),
                        "current_mid": round(current_mid, 4) if current_mid > 0 else merged_plan.get("current_mid"),
                        "selected_option_mid": round(current_mid, 4) if current_mid > 0 else merged_plan.get("selected_option_mid"),
                        "remaining_quantity": row_open_quantity,
                        "broker_position_quantity": position_qty,
                        "unrealized_pnl": round(unrealized_pnl, 2),
                        "marked_pnl": marked_pnl,
                        "option_lifecycle": "runner_open" if bool(merged_plan.get("partial_exit_taken")) else "position_open",
                    }
                )
                update_fields["entry_price"] = position_entry if position_entry > 0 else update_fields.get("entry_price")
                update_fields["pnl"] = marked_pnl
                if current_status in OPTION_EXIT_PENDING_STATUSES and option_symbol not in open_sell_symbols:
                    update_fields["status"] = "position_open"
                elif current_status not in OPTION_EXIT_PENDING_STATUSES:
                    update_fields["status"] = "position_open"
            else:
                if current_status in OPTION_EXIT_PENDING_STATUSES:
                    merged_plan["remaining_quantity"] = 0
                    merged_plan["unrealized_pnl"] = 0.0
                    merged_plan["option_lifecycle"] = "closed"
                    update_fields["status"] = "closed"
                    update_fields["closed_at"] = row.get("closed_at") or datetime.now(timezone.utc).isoformat()
                    if row.get("exit_price") is None:
                        exit_price = self._safe_float(
                            merged_plan.get("runner_exit_price"),
                            merged_plan.get("current_mid") or row.get("entry_price") or 0.0,
                        )
                        if exit_price > 0:
                            update_fields["exit_price"] = exit_price
                elif current_status == "position_open" and broker_order and str(getattr(broker_order, "status", "")).strip().lower() == "filled":
                    update_fields["status"] = "closed"
                    update_fields["closed_at"] = row.get("closed_at") or datetime.now(timezone.utc).isoformat()

            analysis_json = json.dumps(merged_plan, default=str) if merged_plan else None
            if analysis_json:
                update_fields["analysis_json"] = analysis_json
            self.repository.update_option_trade(
                client_order_id=client_order_id,
                **update_fields,
            )
        return snapshot

    def _submit_option_exit_order(
        self,
        row: dict,
        quantity: float,
        current_mid: float,
        exit_reason: str,
        plan: dict,
        remaining_after_fill: float | None = None,
        pnl: float | None = None,
    ) -> dict:
        option_symbol = self.option_client.normalize_option_symbol(row.get("option_symbol") or "")
        if not option_symbol:
            return {"ok": False, "reason": "Missing option symbol for exit order."}
        exit_qty = max(int(round(float(quantity or 0))), 1)
        limit_price = round(max(float(current_mid or 0.0), 0.01), 2)
        if limit_price <= 0:
            return {"ok": False, "reason": "Current option mark is unavailable for exit."}
        client_order_id = self._option_client_order_id(row.get("underlying_symbol") or "OPTION", prefix="option-exit")
        try:
            order = self.option_client.submit_option_limit_order(
                symbol=option_symbol,
                qty=exit_qty,
                limit_price=limit_price,
                client_order_id=client_order_id,
                position_intent="sell_to_close",
            )
        except Exception as exc:
            return {"ok": False, "reason": f"Alpaca option exit rejected: {exc}"}

        remaining_before_exit = self._safe_float(plan.get("remaining_quantity"), row.get("quantity") or 0.0)
        remaining_after_exit = max(
            self._safe_float(remaining_after_fill, remaining_before_exit - exit_qty),
            0.0,
        )
        broker_status = str(getattr(order, "status", "") or "").strip().lower()
        journal_status = "exit_pending"
        closed_at = None
        if broker_status == "filled":
            journal_status = "closed" if remaining_after_exit <= 0 else "position_open"
            closed_at = datetime.now(timezone.utc).isoformat() if remaining_after_exit <= 0 else None
        updated_plan = {
            **plan,
            "active_exit_order_id": str(getattr(order, "id", "") or ""),
            "active_exit_client_order_id": client_order_id,
            "active_exit_order_qty": exit_qty,
            "active_exit_order_price": limit_price,
            "active_exit_reason": exit_reason,
            "active_exit_submitted_at": _serialize_value(getattr(order, "submitted_at", None)),
            "broker_status": broker_status,
            "runner_exit_price": round(limit_price, 4),
            "runner_exit_reason": exit_reason,
            "remaining_quantity": remaining_after_exit if broker_status == "filled" else remaining_before_exit,
            "unrealized_pnl": 0.0 if broker_status == "filled" and remaining_after_exit <= 0 else plan.get("unrealized_pnl"),
            "option_lifecycle": "closed" if journal_status == "closed" else ("runner_open" if remaining_after_exit > 0 and plan.get("partial_exit_taken") else plan.get("option_lifecycle")),
        }
        self.repository.update_option_trade(
            client_order_id=row["client_order_id"],
            status=journal_status,
            exit_price=limit_price,
            closed_at=closed_at,
            pnl=pnl,
            analysis_json=json.dumps(updated_plan, default=str),
            notes=exit_reason,
        )
        return {
            "ok": True,
            "client_order_id": client_order_id,
            "broker_order_id": str(getattr(order, "id", "") or ""),
            "status": str(getattr(order, "status", "") or "").strip().lower(),
            "limit_price": limit_price,
            "quantity": exit_qty,
            "journal_status": journal_status,
        }

    def _option_marked_pnl(self, entry_price: float, current_mid: float, remaining_quantity: float, realized_pnl: float) -> float:
        open_pnl = (current_mid - entry_price) * remaining_quantity * OPTION_CONTRACT_MULTIPLIER
        return round(realized_pnl + open_pnl, 2)

    def _close_option_trade(self, row: dict, exit_price: float, exit_reason: str, plan: dict) -> dict:
        entry_price = float(row.get("entry_price") or plan.get("entry_mid") or 0.0)
        remaining_quantity = float(plan.get("remaining_quantity") or row.get("quantity") or 0.0)
        realized_pnl = float(plan.get("realized_pnl") or 0.0)
        pnl = self._option_marked_pnl(entry_price, exit_price, remaining_quantity, realized_pnl)
        updated_plan = {
            **plan,
            "option_lifecycle": "closed",
            "current_mid": round(exit_price, 4),
            "remaining_quantity": 0,
            "marked_pnl": pnl,
            "unrealized_pnl": 0.0,
            "runner_exit_price": round(exit_price, 4),
            "runner_exit_reason": exit_reason,
        }
        self.repository.update_option_trade_plan(
            client_order_id=row["client_order_id"],
            notes=exit_reason,
            analysis_json=json.dumps(updated_plan, default=str),
        )
        self.repository.update_option_trade_status(
            client_order_id=row["client_order_id"],
            status="closed",
            pnl=pnl,
            exit_price=round(exit_price, 4),
            closed_at=datetime.now(timezone.utc).isoformat(),
            notes=exit_reason,
        )
        return {"symbol": row.get("underlying_symbol"), "contract": row.get("option_symbol"), "reason": exit_reason, "pnl": pnl}

    def _manage_option_paper_trades_current_account(self) -> dict:
        snapshot = self._sync_option_broker_state()
        open_trades = self.repository.get_open_option_trades(profile_id=self._option_account_profile_id())
        if open_trades.empty:
            return {"managed": 0, "closed": [], "updated": []}
        if not self._option_market_hours_open():
            return {"managed": 0, "closed": [], "updated": [], "blocked": "outside_regular_us_market_hours"}

        position_map = snapshot.get("position_map", {})
        open_sell_symbols = snapshot.get("open_sell_symbols", set())
        managed = 0
        closed: list[dict] = []
        updated: list[str] = []
        managed_contracts: set[str] = set()
        for row in open_trades.to_dict("records"):
            status = str(row.get("status") or "").lower()
            if status not in OPTION_AUTO_ACTIVE_STATUSES or status in OPTION_ENTRY_PENDING_STATUSES:
                continue

            option_symbol = self.option_client.normalize_option_symbol(row.get("option_symbol") or "")
            if option_symbol in managed_contracts:
                continue
            managed_contracts.add(option_symbol)
            position = position_map.get(option_symbol)
            if not option_symbol or not position or option_symbol in open_sell_symbols:
                continue

            plan = self._option_trade_plan_state(row)
            quote = self._option_quote_for_trade(row)
            entry_price = self._safe_float(
                getattr(position, "avg_entry_price", None),
                row.get("entry_price") or plan.get("entry_mid") or 0.0,
            )
            quantity = abs(self._safe_float(getattr(position, "qty", None), row.get("quantity") or 0.0))
            current_mid = self._safe_float(
                getattr(position, "current_price", None),
                quote.get("mid") if quote else 0.0,
            )
            if entry_price <= 0 or quantity <= 0 or current_mid <= 0:
                continue

            remaining_quantity = float(quantity)
            realized_pnl = self._safe_float(plan.get("realized_pnl"), 0.0)
            stop_price = self._safe_float(plan.get("runner_stop"), row.get("stop_price") or 0.0)
            stop_loss_mode = str(plan.get("stop_loss_mode") or ("manual_percent" if stop_price > 0 else "ema20_candle"))
            target_price = self._safe_float(plan.get("take_profit_1"), row.get("target_price") or 0.0)
            partial_exit_taken = bool(plan.get("partial_exit_taken"))
            target_1_taken_this_cycle = False
            managed += 1
            exit_reason = ""
            exit_qty = 0.0
            ema20_break = self._option_ema20_underlying_break(row.get("underlying_symbol") or row.get("symbol") or "") if stop_loss_mode == "ema20_candle" else None
            underlying_target_strike = self._safe_float(plan.get("underlying_target_1_strike"), 0.0)
            underlying_last_price = (
                self._option_underlying_last_price(row.get("underlying_symbol") or row.get("symbol") or "")
                if underlying_target_strike > 0 and not partial_exit_taken
                else 0.0
            )
            underlying_target_hit = underlying_target_strike > 0 and underlying_last_price >= underlying_target_strike

            if stop_price > 0 and current_mid <= stop_price:
                exit_reason = "runner_stop_hit" if partial_exit_taken else "option_stop_loss_hit"
                exit_qty = remaining_quantity
            elif ema20_break:
                exit_reason = str(ema20_break.get("reason") or "option_underlying_5m_ema20_break")
                exit_qty = remaining_quantity
                plan = {
                    **plan,
                    "ema20_stop": ema20_break,
                    "runner_exit_reason": exit_reason,
                }
            elif underlying_target_hit:
                target_qty = float(plan.get("contracts_to_sell_at_target_1") or self._option_target_1_contracts(int(quantity)))
                target_qty = min(max(target_qty, 1.0), remaining_quantity)
                if target_qty >= remaining_quantity:
                    exit_reason = "underlying_liquidity_strike_target_1_hit"
                    exit_qty = remaining_quantity
                else:
                    realized_pnl += (current_mid - entry_price) * target_qty * OPTION_CONTRACT_MULTIPLIER
                    remaining_quantity -= target_qty
                    partial_exit_taken = True
                    target_1_taken_this_cycle = True
                    stop_price = round(entry_price, 4)
                    plan = {
                        **plan,
                        "partial_exit_taken": True,
                        "partial_exit_price": round(current_mid, 4),
                        "partial_exit_qty": target_qty,
                        "remaining_quantity": remaining_quantity,
                        "realized_pnl": round(realized_pnl, 2),
                        "runner_stop": stop_price,
                        "runner_stop_locked_pct": 0.0,
                        "underlying_target_1_hit_price": round(underlying_last_price, 4),
                        "option_lifecycle": "runner_open",
                    }
                    exit_reason = "underlying_liquidity_strike_target_1_hit"
                    exit_qty = target_qty
            elif target_price > 0 and current_mid >= target_price and not partial_exit_taken:
                target_qty = float(plan.get("contracts_to_sell_at_target_1") or self._option_target_1_contracts(int(quantity)))
                target_qty = min(max(target_qty, 1.0), remaining_quantity)
                if target_qty >= remaining_quantity:
                    exit_reason = "option_target_1_hit"
                    exit_qty = remaining_quantity
                else:
                    realized_pnl += (current_mid - entry_price) * target_qty * OPTION_CONTRACT_MULTIPLIER
                    remaining_quantity -= target_qty
                    partial_exit_taken = True
                    target_1_taken_this_cycle = True
                    stop_price = round(entry_price, 4)
                    plan = {
                        **plan,
                        "partial_exit_taken": True,
                        "partial_exit_price": round(current_mid, 4),
                        "partial_exit_qty": target_qty,
                        "remaining_quantity": remaining_quantity,
                        "realized_pnl": round(realized_pnl, 2),
                        "runner_stop": stop_price,
                        "runner_stop_locked_pct": 0.0,
                        "option_lifecycle": "runner_open",
                    }
                    exit_reason = "partial_take_profit_1"
                    exit_qty = target_qty

            if partial_exit_taken and remaining_quantity > 0 and not target_1_taken_this_cycle:
                target_pct = self._safe_float(plan.get("first_profit_target_percent"), DEFAULT_OPTION_FIRST_TARGET_PCT)
                runner_lock_step_pct = self._safe_float(
                    plan.get("runner_lock_step_percent"),
                    DEFAULT_OPTION_RUNNER_LOCK_STEP_PCT,
                )
                if runner_lock_step_pct <= 0:
                    runner_lock_step_pct = DEFAULT_OPTION_RUNNER_LOCK_STEP_PCT
                gain_pct = ((current_mid - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
                if gain_pct >= target_pct + runner_lock_step_pct:
                    locked_pct = int((gain_pct - target_pct) // runner_lock_step_pct) * runner_lock_step_pct
                    stop_price = max(stop_price, round(entry_price * (1 + (locked_pct / 100.0)), 4))
                    plan = {
                        **plan,
                        "runner_lock_step_percent": runner_lock_step_pct,
                        "runner_locked_profit_percent": locked_pct,
                        "runner_stop": stop_price,
                        "runner_stop_locked_pct": round(((stop_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 0.0,
                    }

            marked_pnl = self._option_marked_pnl(entry_price, current_mid, remaining_quantity, realized_pnl)
            updated_plan = {
                **plan,
                "current_mid": round(current_mid, 4),
                "selected_option_bid": quote.get("bid") if quote else plan.get("selected_option_bid"),
                "selected_option_ask": quote.get("ask") if quote else plan.get("selected_option_ask"),
                "selected_option_mid": round(current_mid, 4),
                "selected_option_delta": quote.get("delta") if quote else plan.get("selected_option_delta"),
                "selected_option_strike": (quote.get("strike_price") if quote else None) or plan.get("selected_option_strike"),
                "selected_option_expiry": (quote.get("expiry_date") if quote else None) or plan.get("selected_option_expiry"),
                "underlying_target_1_strike": underlying_target_strike if underlying_target_strike > 0 else plan.get("underlying_target_1_strike"),
                "underlying_target_1_last_price": round(underlying_last_price, 4) if underlying_last_price > 0 else plan.get("underlying_target_1_last_price"),
                "remaining_quantity": remaining_quantity,
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round((current_mid - entry_price) * remaining_quantity * OPTION_CONTRACT_MULTIPLIER, 2),
                "marked_pnl": marked_pnl,
            }
            if exit_reason:
                remaining_after_fill = (
                    remaining_quantity
                    if target_1_taken_this_cycle and remaining_quantity > 0
                    else max(remaining_quantity - exit_qty, 0.0)
                )
                exit_result = self._submit_option_exit_order(
                    row,
                    exit_qty,
                    current_mid,
                    exit_reason,
                    updated_plan,
                    remaining_after_fill=remaining_after_fill,
                    pnl=marked_pnl,
                )
                if exit_result.get("ok"):
                    closed.append(
                        {
                            "symbol": row.get("underlying_symbol"),
                            "contract": row.get("option_symbol"),
                            "reason": exit_reason,
                            "pnl": marked_pnl,
                            "qty": exit_qty,
                        }
                    )
                    updated.append(str(row.get("option_symbol") or ""))
                    continue
                self.repository.update_option_trade(
                    client_order_id=row["client_order_id"],
                    status="position_open",
                    pnl=marked_pnl,
                    stop_price=stop_price if stop_price > 0 else None,
                    target_price=target_price if target_price > 0 else None,
                    analysis_json=json.dumps(updated_plan, default=str),
                    notes=str(exit_result.get("reason") or "option_exit_submit_failed"),
                )
                continue

            self.repository.update_option_trade(
                client_order_id=row["client_order_id"],
                status="position_open",
                pnl=marked_pnl,
                stop_price=stop_price if stop_price > 0 else None,
                target_price=target_price if target_price > 0 else None,
                analysis_json=json.dumps(updated_plan, default=str),
                notes="option_auto_manage",
            )
            updated.append(str(row.get("option_symbol") or ""))

        if managed:
            self.repository.log_bot_event(
                "option_auto_manage",
                f"Option Alpaca paper engine managed {managed} open contract(s), submitted exits for {len(closed)}.",
                json.dumps({"updated": updated[:10], "closed": closed}, default=str),
            )
        return {"managed": managed, "closed": closed, "updated": updated}

    def manage_option_paper_trades(self) -> dict:
        if not getattr(self, "option_contexts", None):
            return self._manage_option_paper_trades_current_account()
        totals = {"managed": 0, "closed": [], "updated": [], "accounts": []}
        with self.option_context_lock:
            original = {
                "profile_id": self.option_profile_id,
                "credentials": self.option_credentials,
                "client": self.option_client,
                "trader": self.option_trader,
            }
            try:
                for profile_id, context in self.option_contexts.items():
                    self.option_profile_id = profile_id
                    self.option_credentials = context["credentials"]
                    self.option_client = context["client"]
                    self.option_trader = context["trader"]
                    result = self._manage_option_paper_trades_current_account()
                    totals["managed"] += int(result.get("managed") or 0)
                    totals["closed"].extend(result.get("closed") or [])
                    totals["updated"].extend(result.get("updated") or [])
                    totals["accounts"].append({"profileId": profile_id, **result})
                    if result.get("blocked") and "blocked" not in totals:
                        totals["blocked"] = result["blocked"]
            finally:
                self.option_profile_id = original["profile_id"]
                self.option_credentials = original["credentials"]
                self.option_client = original["client"]
                self.option_trader = original["trader"]
        return totals

    def manage_option_positions_now(self) -> dict:
        response = self.manage_option_paper_trades()
        managed = int(response.get("managed") or 0)
        closed = response.get("closed") or []
        blocked = str(response.get("blocked") or "").strip()
        if blocked:
            status = "blocked"
            self.option_bot_message = f"Option stop manager blocked: {blocked}."
        elif managed:
            status = "managed"
            self.option_bot_message = (
                f"Option stop manager checked {managed} open option position(s), "
                f"submitted exits for {len(closed)}."
            )
        else:
            status = "idle"
            self.option_bot_message = "Option stop manager checked Alpaca; no open option position needed an exit."
        self.action_message = self.option_bot_message
        result = {"status": status, "message": self.option_bot_message, **response}
        self.repository.log_bot_event(
            "option_manage_now",
            self.action_message,
            json.dumps(result, default=str),
        )
        return {
            "result": _serialize_value(result),
            "dashboard": self.dashboard_payload(),
        }

    def _plan_option_paper_trades(self, candidate_frame: pd.DataFrame | None = None) -> dict:
        self.option_plan_blocks = []
        candidates = candidate_frame if candidate_frame is not None else self.option_candidate_results
        if candidates is None or candidates.empty:
            self.option_bot_message = "Option planner found no signal candidates on the option watchlist."
            self.option_plan_blocks = [{"reason": "no signal candidates on the option watchlist"}]
            return {"created": 0, "blocked": self.option_plan_blocks, "planned": []}

        if not self._option_entry_window_open():
            self.option_bot_message = "Option bot is armed, but new option tickets are limited to 9:30 AM through 3:44:59 PM ET. Monitoring and exits continue until 4:00 PM ET."
            self.option_plan_blocks = [{"reason": "outside_option_entry_window"}]
            return {"created": 0, "blocked": self.option_plan_blocks, "planned": []}

        eligible = candidates.copy()
        if "allowed" in eligible.columns:
            eligible = eligible[eligible["allowed"]]
        if eligible.empty:
            self.option_bot_message = "Option planner found no qualified underlyings after rule checks."
            self.option_plan_blocks = [{"reason": "no underlyings passed option entry rules"}]
            return {"created": 0, "blocked": self.option_plan_blocks, "planned": []}

        option_sort_columns = [
            column
            for column in [
                "entry",
            ]
            if column in eligible.columns
        ]
        if option_sort_columns:
            eligible = eligible.sort_values(option_sort_columns, ascending=[False] * len(option_sort_columns))

        option_daily_amount = float(self.option_risk_settings.get("dailyTradeAmount") or 0)
        option_trade_amount = float(self.option_risk_settings.get("tradeAmount") or 0)
        max_new_trades = len(eligible)
        if option_daily_amount > 0 and option_trade_amount > 0:
            max_new_trades = max(int(option_daily_amount // option_trade_amount), 0)
        if max_new_trades == 0 and option_daily_amount > 0 and option_trade_amount > 0:
            self.option_bot_message = "Option planner is blocked because daily trade amount is smaller than one option trade amount."
            self.option_plan_blocks = [{"reason": "daily option capital smaller than per-trade amount"}]
            return {"created": 0, "blocked": self.option_plan_blocks, "planned": []}
        buying_power_by_profile: dict[str, float] = {}
        reserved_by_profile: dict[str, float] = {}
        active_underlyings = self._active_option_trade_underlyings()

        created = 0
        blocked: list[dict] = []
        planned: list[str] = []
        option_watchlist_set = set(self._option_bot_trade_universe())
        for row in eligible.to_dict("records"):
            symbol = self._normalize_option_symbol(row.get("symbol", ""))
            if not symbol:
                continue
            if symbol not in option_watchlist_set:
                blocked.append({"symbol": symbol, "reason": f"not in selected {self._option_watchlist_source_label()}"})
                continue
            if symbol in active_underlyings:
                blocked.append({"symbol": symbol, "reason": "option trade already active for underlying"})
                continue
            if created >= max_new_trades:
                blocked.append({"symbol": symbol, "reason": "option daily trade amount reached"})
                continue

            if str(row.get("oi_priority_label") or "").strip() == "A+ HOT":
                selected_contract, selection_error = self._option_contract_from_a_plus_snapshot(row)
            else:
                selected_contract, selection_error = self._select_option_contract(symbol)
            if not selected_contract:
                blocked.append({"symbol": symbol, "reason": selection_error or "no valid option contract"})
                continue

            contract_cost = selected_contract["mid"] * OPTION_CONTRACT_MULTIPLIER
            if option_trade_amount > 0 and contract_cost > option_trade_amount:
                blocked.append({"symbol": symbol, "reason": f"contract cost ${contract_cost:.2f} exceeds per-trade option amount"})
                continue

            quantity = self._option_contract_quantity(selected_contract["mid"])
            order_cost = self._option_order_cost(selected_contract["mid"], quantity)
            option_context = self._option_context_for_underlying(symbol)
            profile_id = option_context["profile_id"]
            if self.option_bot_config["approvalMode"] == "automatic" and profile_id not in buying_power_by_profile:
                account, gate_error = self._option_account_gate(0.0, option_client=option_context["client"])
                if gate_error:
                    blocked.append({"symbol": symbol, "reason": f"{profile_id}: {gate_error}"})
                    continue
                buying_power_by_profile[profile_id] = self._option_tradeable_buying_power(account)
                reserved_by_profile[profile_id] = 0.0
            available_buying_power = buying_power_by_profile.get(profile_id)
            reserved_buying_power = reserved_by_profile.get(profile_id, 0.0)
            if available_buying_power is not None and reserved_buying_power + order_cost > available_buying_power + 0.01:
                blocked.append(
                    {
                        "symbol": symbol,
                        "reason": (
                            f"{profile_id} reserved option order cost ${reserved_buying_power + order_cost:.2f} "
                            f"exceeds Alpaca option buying power ${available_buying_power:.2f}"
                        ),
                    }
                )
                continue
            option_plan = self._option_initial_trade_plan(selected_contract["mid"], quantity, selected_contract, row)
            stop_label = option_plan.get("stop_loss_label") or (
                f"{option_plan['runner_stop']:.2f}" if option_plan.get("runner_stop") else "5-minute candle close below EMA20"
            )
            entry_status = "position_open" if self.option_bot_config["approvalMode"] == "automatic" else "awaiting_approval"
            note = (
                "Planned from option bot scan. "
                f"Approval={self.option_bot_config['approvalMode']}. "
                f"Trigger={row.get('option_rule_trigger_match') or row.get('setup_name') or 'none'}. "
                f"Selected {quantity} {selected_contract['symbol']} at mid {selected_contract['mid']:.2f}. "
                f"Delta={selected_contract['delta']:.4f}. "
                f"Expected move={selected_contract['expected_move']:.2f}. "
                f"OI wall={selected_contract.get('call_wall_strike') or 'none'} "
                f"({selected_contract.get('oi_wall_signal') or 'no wall signal'}). "
                f"Spread={selected_contract['spread']:.2f}. "
                f"Stop={stop_label}. Target1={option_plan['take_profit_1']:.2f}. "
                f"Target1 sell contracts={option_plan['contracts_to_sell_at_target_1']}."
            )
            submission = self._submit_option_trade_request(
                underlying_symbol=symbol,
                option_symbol=selected_contract["symbol"],
                structure="Only Long Call",
                quantity=quantity,
                entry_price=selected_contract["mid"],
                stop_price=option_plan["runner_stop"],
                target_price=option_plan["take_profit_1"],
                max_loss_amount=order_cost,
                notes=note,
                trigger_source="option_bot_scan",
                status=entry_status,
                analysis_overrides={
                    **option_plan,
                    "option_rule_price_pass": row.get("option_rule_price_pass"),
                    "option_rule_live_four_hour_volume_pass": row.get("option_rule_live_four_hour_volume_pass"),
                    "option_rule_trigger_pass": row.get("option_rule_trigger_pass"),
                    "option_rule_all_passed": row.get("option_rule_all_passed"),
                    "option_rule_any_passed": row.get("option_rule_any_passed"),
                    "option_rule_passed": row.get("option_rule_passed"),
                    "option_rule_trigger_match": row.get("option_rule_trigger_match"),
                    "option_live_four_hour_volume_change_pct": row.get("option_live_four_hour_volume_change_pct"),
                    "option_live_four_hour_current_volume": row.get("option_live_four_hour_current_volume"),
                    "option_live_four_hour_volume_2_bars_ago": row.get("option_live_four_hour_volume_2_bars_ago"),
                },
                submit_to_broker=self.option_bot_config["approvalMode"] == "automatic",
                selected_contract_override=selected_contract,
                account_gate_prechecked=self.option_bot_config["approvalMode"] == "automatic",
            )
            if not submission.get("ok"):
                blocked.append({"symbol": symbol, "reason": submission.get("reason") or "option order submission failed"})
                continue
            reserved_by_profile[profile_id] = reserved_by_profile.get(profile_id, 0.0) + order_cost
            active_underlyings.add(symbol)
            created += 1
            planned.append(symbol)

        if created:
            mode_label = "Alpaca auto-submit" if self.option_bot_config["approvalMode"] == "automatic" else "human approval queue"
            self.option_bot_message = f"Option planner prepared {created} option trade ticket(s) for {', '.join(planned[:6])}. Mode: {mode_label}."
        else:
            self.option_bot_message = "Option planner did not create new Alpaca option tickets because each qualified underlying already has an active option record or was blocked by capital or broker rules."
        self.option_plan_blocks = blocked
        return {"created": created, "blocked": blocked, "planned": planned}

    def select_account(self, profile_id: str) -> dict:
        requested = str(profile_id or "").strip().lower()
        available_ids = {item["id"].lower() for item in self.available_accounts()}
        if requested not in available_ids:
            return {"error": f"Unknown account profile: {profile_id}"}
        if settings.is_option_account_profile(requested, self.client.mode):
            return {
                "error": (
                    f"{self._option_account_credentials().label} is reserved for option paper trading. "
                    "Stock trading is blocked on this account."
                )
            }

        was_running = self.scheduler_enabled
        self.scheduler_enabled = False
        self.bot_state = "Paused"
        time.sleep(0.2)
        self._rebuild_runtime(requested)
        self.scan_results = pd.DataFrame()
        self.candidate_results = pd.DataFrame()
        self.scan_timestamp = None
        self.action_message = f"Active account switched to {self.client.credentials.label}."
        self.repository.log_bot_event("account_switch", self.action_message)
        if was_running:
            self.bot_state = "Running"
            self.scheduler_enabled = True
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
        return self.dashboard_payload()

    def _stock_account_block_message(self) -> str:
        if settings.is_option_account_profile(self.active_profile_id, self.client.mode):
            return (
                f"{self._option_account_credentials().label} is reserved for option paper trading. "
                "Stock trading is blocked on this account."
            )
        return ""

    def scan(
        self,
        symbols: list[str] | None = None,
        scan_label: str = "Watchlist",
        return_payload: bool = True,
        merge_results: bool = False,
    ) -> dict:
        with self.scan_lock:
            universe = [str(symbol).strip().upper() for symbol in (symbols or self.scanner.settings.default_universe) if str(symbol).strip()]
            label = str(scan_label or "Watchlist").strip() or "Watchlist"
            is_mag7 = label == "MAG7-Watchlist Options"
            self.client.ensure_streaming(universe + ["SPY"])
            rvol_threshold = (
                self.scanner.settings.tos_rvol_mag7_num_dev
                if is_mag7
                else self.scanner.settings.tos_rvol_num_dev
            )
            batch_results = self.scanner.run_tos_scan(
                symbols=universe,
                max_results=50,
                rvol_confirmation_threshold=rvol_threshold,
            )
            scan_results = batch_results
            if merge_results and not is_mag7:
                prior = getattr(self, "scan_results", pd.DataFrame())
                if prior is not None and not prior.empty and "symbol" in prior.columns:
                    batch_symbols = set(universe)
                    retained = prior[
                        ~prior["symbol"].fillna("").astype(str).str.upper().isin(batch_symbols)
                    ].copy()
                    scan_results = pd.concat([retained, batch_results], ignore_index=True, sort=False)
                    sort_columns = [
                        column for column in ("one_hour_price_change_pct", "four_hour_volume_change_pct")
                        if column in scan_results.columns
                    ]
                    if sort_columns:
                        scan_results = scan_results.sort_values(
                            sort_columns,
                            ascending=[False] * len(sort_columns),
                        ).reset_index(drop=True)
            candidate_results = scan_results.copy()
            timestamp = datetime.now().astimezone()
            if is_mag7:
                self.mag7_scan_results = scan_results
                self.mag7_candidate_results = candidate_results
                self.mag7_scan_timestamp = timestamp
            else:
                self.scan_results = scan_results
                self.candidate_results = candidate_results
                self.scan_timestamp = timestamp
            self.repository.log_scan_run(
                len(universe),
                len(batch_results),
                None if batch_results.empty else str(batch_results.iloc[0].get("symbol", "")),
                f"5m yellow/cyan C/CALL 2H/4H scanner: {label}",
            )
            self.repository.log_scanner_history(batch_results, timestamp, source=label)
            self._record_learning_observations(
                batch_results,
                source=f"stock_scanner:{label}",
                product="stock",
                observed_at=timestamp,
            )
            session_status = self._session_status(self.client.get_clock())
            if batch_results.empty:
                if session_status["currentSession"] == "Closed":
                    self.action_message = f"{label} scan completed, but no US stock session is active right now, so there are no live stock setups."
                else:
                    self.action_message = (
                        f"{label} scan completed for {len(universe)} symbols, "
                        "but no tickers matched Last >= $3 and a same-day yellow/cyan C2H, CALL2H, C4H, or CALL4H signal."
                    )
            else:
                self.action_message = (
                    f"5m yellow/cyan MTF {label} scan completed for {len(universe)} symbols. "
                    f"Found {len(batch_results)} matches."
                )
            self.repository.log_bot_event("scan", self.action_message)
            if not return_payload:
                return {
                    "resultCount": len(batch_results),
                    "activeResultCount": len(scan_results),
                    "actionMessage": self.action_message,
                    "scanLabel": label,
                }
        return self.dashboard_payload()

    def scan_oi_watchlist(
        self,
        symbols: list[str] | None = None,
        scan_label: str = "MAG7 OI Scanner",
        return_payload: bool = True,
        merge_results: bool = False,
    ) -> dict:
        normalized_label = str(scan_label or "MAG7 OI Scanner").strip() or "MAG7 OI Scanner"
        with self._oi_scan_lock(normalized_label):
            payload = self._execute_oi_scan(
                symbols=symbols,
                scan_label=normalized_label,
                event_name="oi_scan",
                merge_results=merge_results,
            )
        if not return_payload:
            return payload
        return self.dashboard_payload()

    def scan_oi_now(
        self,
        symbols: list[str] | None = None,
        scan_label: str = "MAG7 OI Scanner",
    ) -> dict:
        normalized_label = str(scan_label or "MAG7 OI Scanner").strip() or "MAG7 OI Scanner"
        universe = self._normalize_option_watchlist(symbols or (
            self._mag7_option_underlyings() if normalized_label == "MAG7 OI Scanner" else self._watchlist_oi_underlyings()
        ))
        lock = self._oi_scan_lock(normalized_label)
        acquired = lock.acquire(timeout=45.0)
        if not acquired:
            existing_rows = self.oi_mag7_scan_results if normalized_label == "MAG7 OI Scanner" else self.oi_watchlist_scan_results
            existing_timestamp = self.oi_mag7_scan_timestamp if normalized_label == "MAG7 OI Scanner" else self.oi_watchlist_scan_timestamp
            self.oi_action_message = (
                f"{normalized_label} is already scanning in the background. "
                "Showing the latest completed results."
            )
            self.action_message = self.oi_action_message
            return {
                "dashboard": {
                    "actionMessage": self.action_message,
                    "oiActionMessage": self.oi_action_message,
                    "oiScanResults": _frame_records(self.oi_scan_results),
                    "oiScanTimestamp": _serialize_value(self.oi_scan_timestamp),
                    "oiMag7ScanResults": _frame_records(self.oi_mag7_scan_results),
                    "oiMag7ScanTimestamp": _serialize_value(self.oi_mag7_scan_timestamp),
                    "oiMag7LastNonEmptyResults": _frame_records(self.oi_mag7_last_non_empty_results),
                    "oiMag7LastNonEmptyTimestamp": _serialize_value(self.oi_mag7_last_non_empty_timestamp),
                    "oiWatchlistScanResults": _frame_records(self.oi_watchlist_scan_results),
                    "oiWatchlistScanTimestamp": _serialize_value(self.oi_watchlist_scan_timestamp),
                    "oiWatchlistLastNonEmptyResults": _frame_records(self.oi_watchlist_last_non_empty_results),
                    "oiWatchlistLastNonEmptyTimestamp": _serialize_value(self.oi_watchlist_last_non_empty_timestamp),
                    "oiManualBusy": True,
                    "oiBusyScanLabel": normalized_label,
                },
                "resultCount": len(existing_rows.index) if isinstance(existing_rows, pd.DataFrame) else 0,
                "actionMessage": self.oi_action_message,
                "scanLabel": normalized_label,
                "symbolCount": len(universe),
                "busy": True,
                "lastCompletedAt": _serialize_value(existing_timestamp),
            }
        try:
            return self._execute_oi_scan(
                symbols=universe,
                scan_label=normalized_label,
                event_name="oi_scan_manual",
            )
        finally:
            lock.release()

    def _oi_scan_lock(self, scan_label: str) -> threading.Lock:
        normalized_label = str(scan_label or "").strip()
        return self.oi_mag7_scan_lock if normalized_label == "MAG7 OI Scanner" else self.oi_watchlist_scan_lock

    def _oi_manual_priority_event(self, scan_label: str) -> threading.Event:
        normalized_label = str(scan_label or "").strip()
        attribute = (
            "oi_mag7_manual_priority_event"
            if normalized_label == "MAG7 OI Scanner"
            else "oi_watchlist_manual_priority_event"
        )
        priority_event = getattr(self, attribute, None)
        if priority_event is None:
            priority_event = threading.Event()
            setattr(self, attribute, priority_event)
        return priority_event

    def _wake_execution_for_oi_rows(self, rows: list[dict]) -> dict:
        option_ready = any(
            str(row.get("priority_label") or "").strip() == "A+ HOT"
            and bool(row.get("trade_eligible"))
            for row in rows
        )
        stock_ready = any(
            str(row.get("priority_label") or "").strip() in {"A+ HOT", "A ACTIVE"}
            and bool(row.get("trade_eligible"))
            and bool(row.get("stock_cloud_alignment_pass"))
            for row in rows
        )
        option_scan_wakeup = getattr(self, "option_scan_wakeup", None)
        if option_scan_wakeup is not None and option_ready:
            option_scan_wakeup.set()
        stock_entry_wakeup = getattr(self, "stock_entry_wakeup", None)
        if stock_entry_wakeup is not None and stock_ready:
            stock_entry_wakeup.set()
        return {"optionReady": option_ready, "stockReady": stock_ready}

    def _execute_oi_scan(
        self,
        symbols: list[str] | None = None,
        scan_label: str = "MAG7 OI Scanner",
        event_name: str = "oi_scan",
        merge_results: bool = False,
    ) -> dict:
        normalized_label = str(scan_label or "MAG7 OI Scanner").strip() or "MAG7 OI Scanner"
        is_mag7 = normalized_label == "MAG7 OI Scanner"
        if not is_mag7:
            raise ValueError("Watchlist OI scanning is disabled. Use the saved Mag7 watchlist scope for OI scans.")
        # Do not trust a caller-supplied universe for this scanner route.
        universe = self._mag7_oi_underlyings()
        rvol_threshold = (
            self.scanner.settings.tos_rvol_mag7_num_dev
            if is_mag7
            else self.scanner.settings.tos_rvol_num_dev
        )
        liquidity_payload = self.scan_option_chain_liquidity(
            symbols=universe,
            min_delta=0.20,
            max_per_symbol=100,
            min_expected_move=2.0,
            allow_zero_dte_after_hours=False,
            min_underlying_price=3.0,
            max_days_to_expiration=OI_SCANNER_MAX_DAYS_TO_EXPIRATION,
            min_one_hour_close_change_pct=None,
            rvol_confirmation_threshold=rvol_threshold,
            relax_price_volume_gates=is_mag7,
            require_mtf_gate=False,
        )
        raw_rows = liquidity_payload.get("rows") or []
        rows = self._collapse_oi_rows(raw_rows)
        timestamp = datetime.now().astimezone()
        result_frame = pd.DataFrame(rows)
        if not result_frame.empty:
            result_frame["first_seen_at"] = timestamp.isoformat()
            result_frame["last_seen_at"] = timestamp.isoformat()
        if is_mag7:
            self.oi_mag7_scan_results = self._merge_daily_oi_results(
                self.oi_mag7_scan_results,
                result_frame,
                timestamp,
            )
            self.oi_mag7_scan_timestamp = timestamp
            if not self.oi_mag7_scan_results.empty:
                self.oi_mag7_last_non_empty_results = self.oi_mag7_scan_results.copy()
                self.oi_mag7_last_non_empty_timestamp = timestamp
        else:
            with self.oi_watchlist_results_lock:
                self.oi_watchlist_scan_results = (
                    self._merge_daily_oi_results(self.oi_watchlist_scan_results, result_frame, timestamp)
                    if merge_results
                    else result_frame
                )
                self.oi_watchlist_scan_timestamp = timestamp
                if not self.oi_watchlist_scan_results.empty:
                    self.oi_watchlist_last_non_empty_results = self.oi_watchlist_scan_results.copy()
                    self.oi_watchlist_last_non_empty_timestamp = timestamp
        self.oi_scan_results = self.oi_mag7_scan_results if is_mag7 else self.oi_watchlist_scan_results
        self.oi_scan_timestamp = timestamp
        if not result_frame.empty:
            history_frame = result_frame.copy()
            history_frame["symbol"] = history_frame["underlying"]
            history_frame["last_price"] = history_frame["underlying_price"]
            history_frame["trigger_source"] = history_frame["scanner_tag"]
            history_frame["setup_name"] = history_frame["setup_type"]
            self.repository.log_scanner_history(history_frame, timestamp, source=normalized_label)
        self._record_learning_observations(
            result_frame,
            source="oi_scanner:mag7" if is_mag7 else "oi_scanner:watchlist",
            product="option",
            observed_at=timestamp,
        )
        if rows:
            self.oi_action_message = (
                f"{normalized_label} completed for {len(universe)} underlyings. "
                f"Found {len(rows)} symbols with 0DTE/core or 1-{OI_SCANNER_MAX_DAYS_TO_EXPIRATION} DTE OTM call matches where price >= $3, "
                "the stock gate passed via the live 5m EMA + VWAP path (yellow/cyan C/CALL 2H/4H is optional), Change % >= 1.00%, "
                "delta >= 0.20, and expected move >= $2."
            )
        elif liquidity_payload.get("errors"):
            self.oi_action_message = (
                f"{normalized_label} completed for {len(universe)} underlyings, "
                "but the option-chain feed returned no usable contracts for the current scan."
            )
        else:
            self.oi_action_message = (
                f"{normalized_label} completed for {len(universe)} underlyings, "
                f"but no 0DTE/core or 1-{OI_SCANNER_MAX_DAYS_TO_EXPIRATION} DTE OTM call contracts matched price >= $3, "
                "the stock gate via the live 5m EMA + VWAP path (yellow/cyan C/CALL 2H/4H is optional), Change % >= 1.00%, "
                "delta >= 0.20, and expected move >= $2."
        )
        self.action_message = self.oi_action_message
        self._wake_execution_for_oi_rows(rows)
        try:
            self._refresh_premarket_plan(as_of=timestamp)
        except Exception as exc:
            # A read-only preparation report may never interrupt scanner or
            # order control flow.
            try:
                self.repository.log_bot_event("premarket_plan_error", str(exc))
            except Exception:
                pass
        self._invalidate_dashboard_cache()
        self.repository.log_bot_event(
            event_name,
            self.oi_action_message,
            json.dumps(
                {
                    "scanLabel": normalized_label,
                    "symbolCount": len(universe),
                    "resultCount": len(rows),
                    "rawResultCount": len(raw_rows),
                    "errors": liquidity_payload.get("errors") or [],
                },
                default=str,
            ),
        )
        return {
            "dashboard": {
                "actionMessage": self.oi_action_message,
                "oiActionMessage": self.oi_action_message,
                "oiScanResults": _frame_records(self.oi_scan_results),
                "oiScanTimestamp": _serialize_value(self.oi_scan_timestamp),
                "oiMag7ScanResults": _frame_records(self.oi_mag7_scan_results),
                "oiMag7ScanTimestamp": _serialize_value(self.oi_mag7_scan_timestamp),
                "oiMag7LastNonEmptyResults": _frame_records(self.oi_mag7_last_non_empty_results),
                "oiMag7LastNonEmptyTimestamp": _serialize_value(self.oi_mag7_last_non_empty_timestamp),
                "oiWatchlistScanResults": _frame_records(self.oi_watchlist_scan_results),
                "oiWatchlistScanTimestamp": _serialize_value(self.oi_watchlist_scan_timestamp),
                "oiWatchlistLastNonEmptyResults": _frame_records(self.oi_watchlist_last_non_empty_results),
                "oiWatchlistLastNonEmptyTimestamp": _serialize_value(self.oi_watchlist_last_non_empty_timestamp),
            },
            "resultCount": len(rows),
            "actionMessage": self.oi_action_message,
            "scanLabel": normalized_label,
            "symbolCount": len(universe),
            "errors": liquidity_payload.get("errors") or [],
        }

    @staticmethod
    def _oi_result_market_date(value) -> date | None:
        if value is None or value == "":
            return None
        try:
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize(EASTERN_TZ)
            else:
                timestamp = timestamp.tz_convert(EASTERN_TZ)
            return timestamp.date()
        except (TypeError, ValueError):
            return None

    def _merge_daily_oi_results(
        self,
        existing: pd.DataFrame,
        incoming: pd.DataFrame,
        observed_at: datetime | None = None,
    ) -> pd.DataFrame:
        market_today = self._oi_result_market_date(observed_at or datetime.now(timezone.utc))
        if existing is not None and not existing.empty and market_today is not None:
            records = existing.to_dict("records")
            records = [
                row
                for row in records
                if (
                    self._oi_result_market_date(row.get("last_seen_at") or row.get("first_seen_at"))
                    in {None, market_today}
                )
            ]
            existing = pd.DataFrame(records)

        if existing is None or existing.empty:
            return incoming.copy() if incoming is not None else pd.DataFrame()
        if incoming is None or incoming.empty:
            return existing.copy()

        existing_records = existing.to_dict("records")
        incoming_records = incoming.to_dict("records")
        positions = {
            str(row.get("underlying") or "").strip().upper(): index
            for index, row in enumerate(existing_records)
            if str(row.get("underlying") or "").strip()
        }
        new_records: list[dict] = []
        for row in incoming_records:
            symbol = str(row.get("underlying") or "").strip().upper()
            if not symbol:
                continue
            if symbol in positions:
                existing_row = existing_records[positions[symbol]]
                row["first_seen_at"] = existing_row.get("first_seen_at") or row.get("first_seen_at")
                existing_records[positions[symbol]] = row
            else:
                new_records.append(row)
        return pd.DataFrame([*new_records, *existing_records])

    def _merge_watchlist_oi_results(self, existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
        return self._merge_daily_oi_results(existing, incoming)

    def _restore_today_oi_results(self) -> None:
        """Rebuild today's stable OI result surfaces after a backend restart."""
        try:
            history, _ = self.repository.get_scanner_history(days=2)
        except Exception:
            return
        if history is None or history.empty:
            return

        market_today = self._oi_result_market_date(datetime.now(timezone.utc))
        if market_today is None:
            return
        history = history.copy()
        history = history[history["scan_date"].astype(str) == market_today.isoformat()]
        if history.empty:
            return

        targets = {
            "MAG7 OI Scanner": (
                "oi_mag7_scan_results",
                "oi_mag7_last_non_empty_results",
                "oi_mag7_scan_timestamp",
                "oi_mag7_last_non_empty_timestamp",
            ),
            "Watchlist OI Scanner": (
                "oi_watchlist_scan_results",
                "oi_watchlist_last_non_empty_results",
                "oi_watchlist_scan_timestamp",
                "oi_watchlist_last_non_empty_timestamp",
            ),
        }
        for source, attributes in targets.items():
            source_rows = history[history["source"].astype(str) == source].sort_values("scanned_at")
            if source_rows.empty:
                continue
            first_seen: dict[str, str] = {}
            latest: dict[str, dict] = {}
            for row in source_rows.to_dict("records"):
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                try:
                    payload = json.loads(row.get("raw_json") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                scanned_at = str(row.get("scanned_at") or "")
                payload["underlying"] = str(payload.get("underlying") or symbol).upper()
                first_seen.setdefault(symbol, scanned_at)
                payload["first_seen_at"] = first_seen[symbol]
                payload["last_seen_at"] = scanned_at
                latest[symbol] = payload
            records = sorted(
                latest.values(),
                key=lambda row: str(row.get("last_seen_at") or ""),
                reverse=True,
            )
            if not records:
                continue
            frame = pd.DataFrame(records)
            latest_timestamp = pd.Timestamp(source_rows.iloc[-1]["scanned_at"]).to_pydatetime()
            current_attr, last_attr, timestamp_attr, last_timestamp_attr = attributes
            setattr(self, current_attr, frame)
            setattr(self, last_attr, frame.copy())
            setattr(self, timestamp_attr, latest_timestamp)
            setattr(self, last_timestamp_attr, latest_timestamp)

    def _record_learning_observations(
        self,
        frame: pd.DataFrame,
        source: str,
        product: str,
        observed_at: datetime | None = None,
        traded: bool = False,
        trade_reference: str | None = None,
    ) -> None:
        """Learning telemetry is deliberately isolated from scanner and order control flow."""
        try:
            if frame is None or frame.empty:
                return
            cohort_map = self._learning_symbol_cohorts()
            scoped_frame = frame.copy()
            symbol_column = next(
                (column for column in ("symbol", "underlying", "underlying_symbol") if column in scoped_frame.columns),
                None,
            )
            if symbol_column is None:
                return
            normalized_symbols = scoped_frame[symbol_column].fillna("").astype(str).str.upper()
            scoped_frame = scoped_frame[normalized_symbols.isin(cohort_map)].copy()
            if scoped_frame.empty:
                return
            scoped_frame["learning_cohort"] = (
                scoped_frame[symbol_column].fillna("").astype(str).str.upper().map(cohort_map)
            )
            capture_at = observed_at or datetime.now().astimezone()
            catalyst_snapshot: dict[str, dict] = {}
            try:
                catalyst_snapshot = self.repository.get_catalyst_snapshot(
                    sorted(set(scoped_frame[symbol_column].fillna("").astype(str).str.upper())),
                    lookback_hours=72,
                    as_of=capture_at,
                )
            except Exception:
                catalyst_snapshot = {}

            def _shadow_value(symbol_value, field, default=None):
                snapshot = catalyst_snapshot.get(str(symbol_value or "").strip().upper()) or {}
                return snapshot.get(field, default)

            # These fields are frozen in the observation JSON for later paired
            # research. The live model's explicit technical whitelist ignores
            # them, so they cannot alter eligibility, rank, size, or timing.
            scoped_frame["catalyst_shadow_coverage"] = scoped_frame[symbol_column].map(
                lambda value: "present" if str(value or "").strip().upper() in catalyst_snapshot else "none"
            )
            scoped_frame["catalyst_shadow_score"] = scoped_frame[symbol_column].map(
                lambda value: _shadow_value(value, "score", 0)
            )
            scoped_frame["catalyst_shadow_sentiment"] = scoped_frame[symbol_column].map(
                lambda value: _shadow_value(value, "sentiment", "Unclassified")
            )
            scoped_frame["catalyst_shadow_published_at"] = scoped_frame[symbol_column].map(
                lambda value: _shadow_value(value, "published_at")
            )
            scoped_frame["catalyst_shadow_first_seen_at"] = scoped_frame[symbol_column].map(
                lambda value: _shadow_value(value, "first_seen_at")
            )
            scoped_frame["catalyst_shadow_age_hours"] = scoped_frame[symbol_column].map(
                lambda value: _shadow_value(value, "age_hours")
            )
            scoped_frame["catalyst_shadow_classifier_version"] = "keyword-v1"
            scoped_frame["catalyst_shadow_information_only"] = True
            self.repository.log_learning_observations(
                scoped_frame,
                source=source,
                product=product,
                observed_at=observed_at,
                traded=traded,
                trade_reference=trade_reference,
            )
        except Exception as exc:
            try:
                self.repository.log_bot_event(
                    "learning_capture_error",
                    f"{source} learning capture failed: {exc}",
                )
            except Exception:
                pass
    def _invalidate_dashboard_cache(self) -> None:
        cache_lock = getattr(self, "dashboard_cache_lock", None)
        if cache_lock is None:
            self.dashboard_cache_timestamp = datetime.now().astimezone() - timedelta(seconds=10)
            return
        with cache_lock:
            # Keep serving the last known payload while a refresh is queued.
            # Clearing it would make the next API request perform slow broker
            # reads synchronously.
            self.dashboard_cache_timestamp = datetime.now().astimezone() - timedelta(seconds=10)

    def start_oi_scan_job(
        self,
        symbols: list[str] | None = None,
        scan_label: str = "MAG7 OI Scanner",
        run_both: bool = False,
    ) -> dict:
        normalized_label = str(scan_label or "MAG7 OI Scanner").strip() or "MAG7 OI Scanner"
        if normalized_label != "MAG7 OI Scanner" or run_both:
            raise ValueError("Watchlist OI scanning is disabled. Use the saved Mag7 watchlist scope for OI scans.")
        universe = self._mag7_oi_underlyings()
        target_lock = self._oi_scan_lock(normalized_label)
        if self.oi_scan_job.get("running"):
            self.oi_action_message = f"{normalized_label} is already scanning. Showing the latest completed results."
            self.action_message = self.oi_action_message
            self._invalidate_dashboard_cache()
            return {
                "dashboard": self._dashboard_control_payload(),
                "scanLabel": normalized_label,
                "symbolCount": len(universe),
                "started": False,
                "busy": True,
            }
        priority_labels = [normalized_label]
        priority_events = [self._oi_manual_priority_event(label) for label in priority_labels]
        for priority_event in priority_events:
            priority_event.set()
        queued_behind_background_scan = target_lock.locked()
        if queued_behind_background_scan:
            self.oi_action_message = (
                f"PRIORITY {normalized_label} queued for {len(universe)} underlyings. "
                "The continuous engine will yield after its active request, then this manual scan runs next."
            )
        else:
            self.oi_action_message = f"PRIORITY {normalized_label} started for {len(universe)} underlyings."
        self.action_message = self.oi_action_message
        self.oi_scan_job = {
            "running": True,
            "scanLabel": normalized_label,
            "symbolCount": len(universe),
            "message": self.oi_action_message,
            "startedAt": datetime.now().astimezone(),
            "finishedAt": None,
            "error": "",
        }
        self._invalidate_dashboard_cache()

        def _runner() -> None:
            try:
                self.scan_oi_watchlist(
                    symbols=universe,
                    scan_label=normalized_label,
                    return_payload=False,
                )
            except Exception as exc:
                self.oi_action_message = f"{normalized_label} failed: {exc}"
                self.action_message = self.oi_action_message
                self.oi_scan_job["error"] = str(exc)
            finally:
                for priority_event in priority_events:
                    priority_event.clear()
                self.oi_scan_job.update({
                    "running": False,
                    "message": self.oi_action_message,
                    "finishedAt": datetime.now().astimezone(),
                })
                self._invalidate_dashboard_cache()

        threading.Thread(target=_runner, daemon=True).start()
        return {
            "dashboard": self._dashboard_control_payload(),
            "scanLabel": normalized_label,
            "symbolCount": len(universe),
            "started": True,
        }

    def _collapse_oi_rows(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return []

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            symbol = str(row.get("underlying") or "").strip().upper()
            if not symbol:
                continue
            grouped.setdefault(symbol, []).append(row)

        collapsed: list[dict] = []
        for symbol, items in grouped.items():
            ranked = sorted(
                items,
                key=lambda row: (
                    int(row.get("days_to_expiration") or 9999),
                    -float(row.get("liquidity_score") or 0.0),
                    -float(row.get("volume_plus_oi") or 0.0),
                    abs(float(row.get("delta") or 0.0) - 0.30),
                    float(row.get("strike") or 0.0),
                ),
            )
            best = dict(ranked[0])
            flow_types = []
            liquidity_winners = []
            setup_types = []
            for item in ranked:
                flow = str(item.get("flow_type") or "").strip()
                liquidity = str(item.get("liquidity_winner") or "").strip()
                setup = str(item.get("setup_type") or "").strip()
                if flow and flow not in flow_types:
                    flow_types.append(flow)
                if liquidity and liquidity not in liquidity_winners:
                    liquidity_winners.append(liquidity)
                if setup and setup not in setup_types:
                    setup_types.append(setup)
            flow_summary = " / ".join(flow_types) if flow_types else str(best.get("flow_type") or "")
            liquidity_summary = " / ".join(liquidity_winners) if liquidity_winners else str(best.get("liquidity_winner") or "")
            setup_summary = " / ".join(setup_types) if setup_types else str(best.get("setup_type") or "")
            best["flow_summary"] = flow_summary
            best["liquidity_summary"] = liquidity_summary
            best["setup_summary"] = setup_summary
            best["scanner_summary_tag"] = f"{flow_summary or '--'} + {liquidity_summary or '--'}"
            best["scanner_tag"] = f"{best.get('flow_type', '--')} + {best.get('liquidity_winner', '--')}"
            best["candidate_count"] = len(items)
            best.update(self._oi_priority_meta(best))
            collapsed.append(best)

        return sorted(
            collapsed,
            key=lambda row: (
                -float(row.get("priority_score") or 0.0),
                -float(row.get("strength_score") or 0.0),
                int(row.get("days_to_expiration") or 9999),
                -float(row.get("liquidity_score") or 0.0),
                str(row.get("underlying") or ""),
            ),
        )

    def _oi_ratio(self, left: object, right: object) -> float:
        left_value = float(left or 0.0)
        right_value = float(right or 0.0)
        if left_value <= 0 and right_value <= 0:
            return 1.0
        if right_value <= 0:
            return 9.99 if left_value > 0 else 1.0
        return left_value / right_value

    def _oi_priority_meta(self, row: dict) -> dict:
        otm_volume = float(row.get("volume") or 0.0)
        atm_volume = float(row.get("atm_volume") or 0.0)
        otm_oi = float(row.get("open_interest") or 0.0)
        atm_oi = float(row.get("atm_open_interest") or 0.0)
        delta = abs(float(row.get("delta") or 0.0))
        expected_move = float(row.get("expected_move") or 0.0)
        change_pct = float(row.get("change_pct", row.get("one_hour_close_change_pct")) or 0.0)
        days_to_expiration = int(row.get("days_to_expiration") or 9999)
        contract_mid = float(row.get("mid") or 0.0)
        premium_traded = max(contract_mid, 0.0) * max(otm_volume, 0.0) * OPTION_CONTRACT_MULTIPLIER
        volume_oi_ratio = self._oi_ratio(otm_volume, otm_oi)
        stock_above_vwap = bool(row.get("stock_above_vwap"))
        stock_ema_stack = bool(row.get("stock_ema_stack"))
        stock_cloud_alignment_pass = bool(row.get("stock_cloud_alignment_pass"))
        stock_volume_trend = bool(row.get("stock_volume_trend"))
        stock_rvol_pass = bool(row.get("stock_tos_rvol_any_pass"))
        stock_mtf_signal_pass = bool(row.get("stock_mtf_bullish_signal_pass"))
        fast_momentum_entry_pass = int(row.get("stock_fast_momentum_score") or 0) >= 2
        five_min_early_only = (
            bool(row.get("stock_tos_rvol_5m_early_alert"))
            and not stock_rvol_pass
            and not fast_momentum_entry_pass
            and not stock_mtf_signal_pass
        )
        strong_rvol = max(
            float(row.get("stock_tos_rvol_5m") or 0.0),
            float(row.get("stock_tos_rvol_15m") or 0.0),
            float(row.get("stock_tos_rvol_30m") or 0.0),
            float(row.get("stock_tos_rvol_1h") or 0.0),
            float(row.get("stock_tos_rvol_2h") or 0.0),
            float(row.get("stock_tos_rvol_4h") or 0.0),
            float(row.get("stock_tos_rvol_1d") or 0.0),
        )

        volume_ratio = self._oi_ratio(otm_volume, atm_volume)
        oi_ratio = self._oi_ratio(otm_oi, atm_oi)

        score = 0
        if volume_ratio >= 4.0:
            score += 35
        elif volume_ratio >= 2.0:
            score += 28
        elif volume_ratio >= 1.25:
            score += 18
        elif volume_ratio > 1.0:
            score += 10

        if oi_ratio >= 4.0:
            score += 35
        elif oi_ratio >= 2.0:
            score += 28
        elif oi_ratio >= 1.25:
            score += 18
        elif oi_ratio > 1.0:
            score += 10

        if stock_above_vwap and stock_ema_stack:
            score += 20
        elif stock_above_vwap or stock_ema_stack:
            score += 10

        if stock_cloud_alignment_pass:
            score += 10

        if stock_mtf_signal_pass:
            score += 20

        if stock_volume_trend:
            score += 6

        fast_momentum_score = int(row.get("stock_fast_momentum_score") or 0)
        if fast_momentum_score >= 3:
            score += 8
        elif fast_momentum_score == 2:
            score += 4

        if stock_rvol_pass:
            score += 10
        if strong_rvol >= 5.0:
            score += 6
        elif strong_rvol >= 3.0:
            score += 3

        if 0.20 <= delta <= 0.45:
            score += 15
        elif 0.15 <= delta <= 0.60:
            score += 8
        elif delta > 0:
            score += 4

        if change_pct >= 1.0:
            score += 10
        elif change_pct >= 0.5:
            score += 6
        elif change_pct > 0:
            score += 3

        if expected_move >= 5.0:
            score += 5
        elif expected_move >= 2.0:
            score += 3

        if volume_oi_ratio >= 2.0:
            score += 10
        elif volume_oi_ratio >= 1.2:
            score += 6
        elif volume_oi_ratio >= 0.8:
            score += 3

        if premium_traded >= 1_000_000:
            score += 8
        elif premium_traded >= 250_000:
            score += 5
        elif premium_traded >= 100_000:
            score += 3

        # OI-wall alignment is confirmation, never a standalone entry gate.
        # It can improve rank only after the stock and option-liquidity inputs exist.
        if bool(row.get("oi_wall_aligned")):
            wall_strength = str(row.get("call_wall_strength") or "").upper()
            if wall_strength == "STRONG":
                score += 5
            elif wall_strength == "MODERATE":
                score += 3

        if days_to_expiration <= 7:
            score += 5
        elif days_to_expiration <= 14:
            score += 2

        score = int(max(min(score, 100), 0))

        # Aggregate summaries describe every candidate strike and may contain
        # both labels even when the selected contract is clean. Keep the
        # selected contract's shape for display, but never use it as a gate.
        selected_flow = str(row.get("flow_type") or "").strip().lower()
        is_mixed_flow = "mixed" in selected_flow or "/" in selected_flow
        volume_direction = 1 if volume_ratio > 1.05 else (-1 if volume_ratio < 0.95 else 0)
        oi_direction = 1 if oi_ratio > 1.05 else (-1 if oi_ratio < 0.95 else 0)
        is_mixed_liquidity = (
            volume_direction != 0
            and oi_direction != 0
            and volume_direction != oi_direction
        )

        if five_min_early_only:
            label = "Watchlist"
            tone = "watch"
            rank = 3
            trade_eligible = False
            display_row = False
        elif score >= 80:
            label = "A+ HOT"
            tone = "hot"
            rank = 5
            trade_eligible = True
            display_row = True
        elif score >= 65:
            label = "A ACTIVE"
            tone = "active"
            rank = 4
            trade_eligible = True
            display_row = True
        elif score >= 50:
            label = "Watchlist"
            tone = "watch"
            rank = 3
            trade_eligible = False
            display_row = False
        elif score >= 35:
            label = "Low Conviction"
            tone = "low"
            rank = 2
            trade_eligible = False
            display_row = False
        else:
            label = "Very Weak"
            tone = "low"
            rank = 1
            trade_eligible = False
            display_row = False

        return {
            "priority_label": label,
            "priority_tone": tone,
            "priority_score": rank,
            "strength_score": score,
            "uw_style_score": score,
            "trade_eligible": trade_eligible,
            "display_row": display_row,
            "signal_shape_label": "Mixed Flow" if (is_mixed_flow or is_mixed_liquidity) else "Clean Flow",
            "rvol_confirmation": "MTF Signal" if stock_mtf_signal_pass else "5m Early" if five_min_early_only else "Confirmed",
            "fast_momentum_entry_pass": fast_momentum_entry_pass,
            "mtf_bullish_signal_pass": stock_mtf_signal_pass,
            "otm_volume_ratio": round(volume_ratio, 2),
            "otm_oi_ratio": round(oi_ratio, 2),
            "volume_oi_ratio": round(volume_oi_ratio, 2),
            "premium_traded": round(premium_traded, 2),
        }

    def _sync_last_stock_scan_from_trader(self) -> None:
        last_scan = getattr(self.trader, "last_scan_results", pd.DataFrame())
        last_candidates = getattr(self.trader, "last_candidate_frame", pd.DataFrame())
        if isinstance(last_scan, pd.DataFrame):
            self.scan_results = last_scan
        if isinstance(last_candidates, pd.DataFrame):
            self.candidate_results = last_candidates
        self.scan_timestamp = datetime.now().astimezone()

    def scan_options(self, create_plans: bool = False, return_payload: bool = True) -> dict:
        active_watchlist = self._option_bot_trade_universe() if create_plans else self._active_option_watchlist()
        watchlist_label = self._option_watchlist_source_label()
        option_hours_open = self._option_market_hours_open()
        option_entry_window_open = self._option_entry_window_open()
        plan_result: dict | None = None
        manage_result: dict | None = None
        if create_plans and option_hours_open:
            manage_result = self.manage_option_paper_trades()
        self.client.ensure_streaming(active_watchlist + ["SPY"])
        signal_source = (
            self._option_signal_frame_from_fresh_oi()
            if create_plans
            else self.option_strategy_frame_for_symbols(active_watchlist, max_results=0)
        )
        signal_candidates = self._apply_option_entry_logic(signal_source)
        option_candidates = self._option_a_plus_hot_candidates(signal_candidates)
        self.option_candidate_results = option_candidates
        self.option_scan_timestamp = datetime.now().astimezone()
        self._record_learning_observations(
            option_candidates,
            source=f"option_bot_candidate:{watchlist_label}",
            product="option",
            observed_at=self.option_scan_timestamp,
        )
        if option_candidates.empty:
            self.action_message = (
                f"Option scan completed for {len(active_watchlist)} underlyings from {watchlist_label}. "
                "No fresh A+ HOT OI-confirmed option setups found."
            )
        else:
            eligible_count = int(option_candidates["allowed"].sum()) if "allowed" in option_candidates.columns else len(option_candidates)
            self.action_message = (
                f"Option scan completed for {len(active_watchlist)} underlyings from {watchlist_label}. "
                f"Found {len(option_candidates)} candidates, {eligible_count} qualified."
            )
        if create_plans:
            if option_entry_window_open:
                plan_result = self._plan_option_paper_trades(option_candidates)
                self.action_message = (
                    f"{self.action_message} Managed {int((manage_result or {}).get('managed') or 0)} open option position(s) first. "
                    f"Logged {plan_result['created']} option ticket(s)."
                )
            elif option_hours_open:
                self.option_bot_message = "Option bot is monitoring, but the 3:45 PM ET new-entry cutoff has passed. Position management and exits remain active until 4:00 PM ET."
                self.action_message = f"{self.action_message} No new option tickets after 3:45 PM ET; managed {int((manage_result or {}).get('managed') or 0)} open position(s)."
            else:
                self.option_bot_message = "Option bot is armed, but it will only create new option tickets during regular US market hours (9:30 AM to 4:00 PM ET)."
                self.action_message = f"{self.action_message} Option bot stayed armed but did not log tickets because the core US market session is closed."
        self._schedule_option_supervisor_report(option_candidates, plan_result, manage_result)
        self.repository.log_bot_event("option_scan", self.action_message)
        self._invalidate_dashboard_cache()
        if not return_payload:
            return {
                "status": "completed",
                "resultCount": len(option_candidates.index),
                "actionMessage": self.action_message,
                "scanLabel": watchlist_label,
            }
        return self.dashboard_payload()

    def start_scan_job(self, symbols: list[str] | None = None, scan_label: str = "Watchlist") -> dict:
        if self.scan_job["running"]:
            return self._dashboard_control_payload()

        universe = [str(symbol).strip().upper() for symbol in (symbols or self.scanner.settings.default_universe) if str(symbol).strip()]
        label = str(scan_label or "Watchlist").strip() or "Watchlist"
        self.scan_job = {
            "running": True,
            "message": f"Scanning {label}: {len(universe)} symbols...",
            "source": label,
            "startedAt": datetime.now().astimezone().isoformat(),
            "finishedAt": None,
            "error": "",
        }
        thread = threading.Thread(target=self._run_scan_job, args=(universe, label), daemon=True)
        thread.start()
        return self._dashboard_control_payload()

    def _run_scan_job(self, symbols: list[str] | None = None, scan_label: str = "Watchlist") -> None:
        try:
            payload = self.scan(symbols=symbols, scan_label=scan_label, return_payload=False)
            result_count = int(payload.get("resultCount") or 0)
            label = str(payload.get("scanLabel") or scan_label or "Watchlist")
            self.scan_job.update(
                {
                    "running": False,
                    "message": f"TOS three-condition {label} scan finished. Found {result_count} matches.",
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "error": "",
                }
            )
        except Exception as exc:
            self.action_message = f"Scan failed: {exc}"
            self.repository.log_bot_event("scan_error", self.action_message)
            self.scan_job.update(
                {
                    "running": False,
                    "message": self.action_message,
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "error": str(exc),
                }
            )

    def _fresh_oi_confirmation(
        self,
        allowed_priority_labels: set[str],
        max_age_seconds: int | None = None,
    ) -> dict:
        max_age_seconds = max(
            int(max_age_seconds or settings.trading.stock_auto_oi_confirmation_max_age_seconds),
            1,
        )
        cutoff = datetime.now().astimezone() - timedelta(seconds=max_age_seconds)
        mag7_symbols = set(self._mag7_option_underlyings())
        confirmed: dict[str, dict] = {}

        for frame, fallback_timestamp in [
            (getattr(self, "oi_mag7_scan_results", pd.DataFrame()), getattr(self, "oi_mag7_scan_timestamp", None)),
            (getattr(self, "oi_watchlist_scan_results", pd.DataFrame()), getattr(self, "oi_watchlist_scan_timestamp", None)),
        ]:
            if frame is None or frame.empty:
                continue
            for row in frame.to_dict("records"):
                symbol = str(row.get("underlying") or row.get("symbol") or "").strip().upper()
                priority_label = str(row.get("priority_label") or "").strip()
                if not symbol or priority_label not in allowed_priority_labels or not bool(row.get("trade_eligible")):
                    continue
                if not bool(row.get("stock_cloud_alignment_pass")):
                    continue
                raw_seen = row.get("last_seen_at") or fallback_timestamp
                try:
                    seen_at = pd.Timestamp(raw_seen)
                    if seen_at.tzinfo is None:
                        seen_at = seen_at.tz_localize(EASTERN_TZ)
                    else:
                        seen_at = seen_at.tz_convert(EASTERN_TZ)
                except Exception:
                    continue
                if seen_at.to_pydatetime() < cutoff:
                    continue
                current = confirmed.get(symbol)
                if current and float(current.get("strength_score") or 0) >= float(row.get("strength_score") or 0):
                    continue
                confirmed[symbol] = {
                    "symbol": symbol,
                    "priority_label": priority_label,
                    "strength_score": int(row.get("strength_score") or 0),
                    "last_seen_at": seen_at.isoformat(),
                    "otm_target": float(row.get("strike") or row.get("otm_strike") or 0),
                    "option_contract": str(row.get("contract") or ""),
                    "signal_snapshot": {
                        "symbol": symbol,
                        "last_price": float(row.get("underlying_price") or 0),
                        "entry": float(row.get("underlying_price") or 0),
                        "setup_name": str(row.get("stock_setup_name") or ""),
                        "trigger_source": str(row.get("stock_trigger_source") or ""),
                        "ema_stack": bool(row.get("stock_ema_stack")),
                        "above_vwap": bool(row.get("stock_above_vwap")),
                        "five_min_cloud_state": row.get("stock_five_min_cloud_state"),
                        "five_min_ema_9": row.get("stock_five_min_ema_9"),
                        "five_min_ema_21": row.get("stock_five_min_ema_21"),
                        "five_min_ema_50": row.get("stock_five_min_ema_50"),
                        "four_hour_cloud_state": row.get("stock_four_hour_cloud_state"),
                        "four_hour_cloud_bullish": bool(row.get("stock_four_hour_cloud_bullish")),
                        "four_hour_ema_9": row.get("stock_four_hour_ema_9"),
                        "four_hour_ema_21": row.get("stock_four_hour_ema_21"),
                        "four_hour_ema_50": row.get("stock_four_hour_ema_50"),
                        "cloud_alignment_pass": bool(row.get("stock_cloud_alignment_pass")),
                        "cloud_alignment_action": row.get("stock_cloud_alignment_action"),
                        "volume_trend": bool(row.get("stock_volume_trend")),
                        "ema9_retest_5m": bool(row.get("stock_ema9_retest_observed")),
                        "allowed": True,
                        "rejection_reason": "",
                    },
                    "contract_snapshot": {
                        "symbol": str(row.get("contract") or ""),
                        "source_symbol": str(row.get("source_contract") or row.get("contract") or ""),
                        "underlying": symbol,
                        "expiry_date": str(row.get("expiry") or ""),
                        "strike_price": float(row.get("strike") or 0),
                        "delta": float(row.get("delta") or 0),
                        "bid": float(row.get("bid") or 0),
                        "ask": float(row.get("ask") or 0),
                        "mid": float(row.get("mid") or 0),
                        "expected_move": float(row.get("expected_move") or 0),
                        "days_to_expiration": int(row.get("days_to_expiration") or 0),
                        "total_volume": float(row.get("volume") or 0),
                        "open_interest": float(row.get("open_interest") or 0),
                        "liquidity_score": float(row.get("liquidity_score") or 0),
                        "liquidity_winner": str(row.get("liquidity_winner") or ""),
                        "liquidity_breakout_level": float(row.get("atm_strike") or 0),
                        "liquidity_breakout_required": bool(row.get("atm_liquidity_dominates_otm")),
                        "liquidity_breakout_passed": (
                            float(row.get("underlying_price") or 0) >= float(row.get("atm_strike") or 0)
                            if float(row.get("atm_strike") or 0) > 0
                            else True
                        ),
                        "liquidity_atm_volume": float(row.get("atm_volume") or 0),
                        "liquidity_atm_open_interest": float(row.get("atm_open_interest") or 0),
                        "liquidity_atm_score": max(
                            float(row.get("atm_volume") or 0),
                            float(row.get("atm_open_interest") or 0),
                        ),
                        "liquidity_atm_dominates_otm": bool(row.get("atm_liquidity_dominates_otm")),
                        "underlying_target_strike": float(row.get("strike") or 0),
                        "underlying_target_volume": float(row.get("volume") or 0),
                        "underlying_target_open_interest": float(row.get("open_interest") or 0),
                        "underlying_target_liquidity_score": float(row.get("liquidity_score") or 0),
                        "underlying_target_liquidity_metric": (
                            "volume"
                            if float(row.get("volume") or 0) >= float(row.get("open_interest") or 0)
                            else "open_interest"
                        ),
                        "call_wall_strike": row.get("call_wall_strike"),
                        "call_wall_open_interest": row.get("call_wall_open_interest"),
                        "call_wall_volume": row.get("call_wall_volume"),
                        "call_wall_concentration": row.get("call_wall_concentration"),
                        "call_wall_strength": row.get("call_wall_strength"),
                        "call_wall_distance_pct": row.get("call_wall_distance_pct"),
                        "put_wall_strike": row.get("put_wall_strike"),
                        "put_wall_open_interest": row.get("put_wall_open_interest"),
                        "put_wall_volume": row.get("put_wall_volume"),
                        "put_wall_concentration": row.get("put_wall_concentration"),
                        "put_wall_strength": row.get("put_wall_strength"),
                        "put_wall_distance_pct": row.get("put_wall_distance_pct"),
                        "oi_wall_signal": row.get("oi_wall_signal"),
                        "oi_wall_aligned": bool(row.get("oi_wall_aligned")),
                    },
                    "rvol_threshold": (
                        float(settings.scanner.tos_rvol_mag7_num_dev)
                        if symbol in mag7_symbols
                        else float(settings.scanner.tos_rvol_num_dev)
                    ),
                }

        rows = sorted(
            confirmed.values(),
            key=lambda row: (-int(row["strength_score"]), row["symbol"]),
        )
        return {
            "symbols": [row["symbol"] for row in rows],
            "thresholds": {row["symbol"]: row["rvol_threshold"] for row in rows},
            "rows": rows,
            "maxAgeSeconds": max_age_seconds,
        }

    def _build_premarket_plan(self, as_of: datetime | None = None) -> dict:
        """Build a read-only opening plan from existing OI memory; never request market data."""
        now = as_of or datetime.now().astimezone()
        if now.tzinfo is None:
            now = pd.Timestamp(now).tz_localize(EASTERN_TZ).to_pydatetime()
        else:
            now = pd.Timestamp(now).tz_convert(EASTERN_TZ).to_pydatetime()
        max_age_seconds = max(
            int(settings.trading.stock_auto_oi_confirmation_max_age_seconds),
            int(settings.trading.option_auto_oi_confirmation_max_age_seconds),
            1,
        )
        fresh_cutoff = now - timedelta(seconds=max_age_seconds)
        latest: dict[str, dict] = {}

        for frame, fallback_timestamp in (
            (getattr(self, "oi_mag7_scan_results", pd.DataFrame()), getattr(self, "oi_mag7_scan_timestamp", None)),
            (getattr(self, "oi_watchlist_scan_results", pd.DataFrame()), getattr(self, "oi_watchlist_scan_timestamp", None)),
        ):
            if frame is None or frame.empty:
                continue
            for raw in frame.to_dict("records"):
                symbol = str(raw.get("underlying") or raw.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                try:
                    seen_at = pd.Timestamp(raw.get("last_seen_at") or fallback_timestamp)
                    if seen_at.tzinfo is None:
                        seen_at = seen_at.tz_localize(EASTERN_TZ)
                    else:
                        seen_at = seen_at.tz_convert(EASTERN_TZ)
                except Exception:
                    continue
                current = latest.get(symbol)
                if current is not None and current["_seen_at"] >= seen_at:
                    continue
                latest[symbol] = {**raw, "_seen_at": seen_at}

        news_by_symbol: dict[str, dict] = {}
        if latest and hasattr(self.repository, "get_catalyst_snapshot"):
            try:
                news_by_symbol = self.repository.get_catalyst_snapshot(
                    sorted(latest),
                    lookback_hours=72,
                    as_of=now,
                )
            except Exception:
                # News is information-only and may never make plan creation or
                # execution unavailable.
                news_by_symbol = {}

        stock_candidates: list[dict] = []
        option_candidates: list[dict] = []
        for symbol, raw in latest.items():
            label = str(raw.get("priority_label") or "").strip()
            trade_eligible = bool(raw.get("trade_eligible"))
            cloud_aligned = bool(raw.get("stock_cloud_alignment_pass"))
            if not (trade_eligible and cloud_aligned and label in {"A+ HOT", "A ACTIVE"}):
                continue
            seen_at = raw["_seen_at"]
            news = news_by_symbol.get(symbol) or {}
            news_context = {
                "coverage": "present" if news else "none",
                "sentiment": str(news.get("sentiment") or "Unclassified") if news else "No recent item",
                "score": int(news.get("score") or 0) if news else 0,
                "headline": str(news.get("headline") or "") if news else "",
                "publishedAt": news.get("published_at"),
                "ageHours": round(float(news.get("age_hours") or 0.0), 2) if news else None,
                "informationOnly": True,
            }
            candidate = {
                "symbol": symbol,
                "priority": label,
                "strengthScore": int(raw.get("strength_score") or 0),
                "lastSeenAt": seen_at.isoformat(),
                "ageSeconds": max(round((now - seen_at.to_pydatetime()).total_seconds(), 1), 0.0),
                "fresh": bool(seen_at.to_pydatetime() >= fresh_cutoff),
                "setup": str(raw.get("stock_setup_name") or ""),
                "trigger": str(raw.get("stock_trigger_source") or ""),
                "underlyingPriceSnapshot": self._safe_float(raw.get("underlying_price"), 0.0),
                "changePctSnapshot": self._safe_float(raw.get("change_pct"), 0.0),
                "fiveMinuteCloud": raw.get("stock_five_min_cloud_state"),
                "fourHourCloud": raw.get("stock_four_hour_cloud_state"),
                "cloudAligned": cloud_aligned,
                "aboveVwap": bool(raw.get("stock_above_vwap")),
                "emaStack": bool(raw.get("stock_ema_stack")),
                "fastMomentumScore": int(raw.get("stock_fast_momentum_score") or 0),
                "rvol": str(raw.get("stock_tos_rvol_timeframes") or ""),
                "contractSnapshot": str(raw.get("contract") or ""),
                "strikeSnapshot": self._safe_float(raw.get("strike"), 0.0),
                "deltaSnapshot": self._safe_float(raw.get("delta"), 0.0),
                "bidSnapshot": self._safe_float(raw.get("bid"), 0.0),
                "askSnapshot": self._safe_float(raw.get("ask"), 0.0),
                "expectedMoveSnapshot": self._safe_float(raw.get("expected_move"), 0.0),
                "daysToExpiration": int(raw.get("days_to_expiration") or 0),
                "news": news_context,
                "orderAuthority": False,
                "openAction": "Wake stock scheduler, then re-fetch live bars and re-run every technical, risk, capital, and duplicate gate.",
            }
            stock_candidates.append(candidate)
            if label == "A+ HOT":
                option_candidates.append(dict(candidate))

        sort_key = lambda row: (
            0 if row["priority"] == "A+ HOT" else 1,
            -int(row["strengthScore"]),
            row["symbol"],
        )
        stock_candidates.sort(key=sort_key)
        option_candidates.sort(key=sort_key)
        completed_cycles = int(getattr(self, "oi_watchlist_completed_cycles", 0) or 0)
        watchlist_total = int(
            getattr(self, "oi_watchlist_universe_count", 0)
            or len(settings.scanner.default_universe)
        )
        watchlist_scanned = int(getattr(self, "oi_watchlist_batch_end", 0) or 0)
        if completed_cycles > 0:
            watchlist_scanned = watchlist_total
        status = "READY" if completed_cycles > 0 else "WARMING"
        message = (
            "Full watchlist OI warm-up completed. Candidates are context only; every order still requires a fresh live recheck."
            if status == "READY"
            else "OI warm-up is still covering the watchlist. Fresh qualifying rows can already wake the live stock recheck."
        )
        return {
            "status": status,
            "generatedAt": now.isoformat(),
            "message": message,
            "coverage": {
                "watchlistScanned": min(max(watchlist_scanned, 0), max(watchlist_total, 0)),
                "watchlistTotal": watchlist_total,
                "completedCycles": completed_cycles,
                "lastCycleDurationSeconds": round(float(getattr(self, "oi_watchlist_cycle_duration_seconds", 0.0) or 0.0), 2),
                "maxCandidateAgeSeconds": max_age_seconds,
            },
            "stockCandidates": stock_candidates[:25],
            "optionCandidates": option_candidates[:25],
            "execution": {
                "shadowOnly": True,
                "usesCachedPlanForOrders": False,
                "liveRevalidationRequired": True,
                "wakesOnFreshStockSignal": True,
                "canBlockTrades": False,
                "canRankTrades": False,
                "canSizeTrades": False,
                "canDelayExecution": False,
            },
            "newsPolicy": {
                "informationOnly": True,
                "canBlockTrades": False,
                "canRankTrades": False,
                "canSizeTrades": False,
                "canDelayExecution": False,
            },
        }

    def _refresh_premarket_plan(self, as_of: datetime | None = None) -> dict:
        plan = self._build_premarket_plan(as_of=as_of)
        lock = getattr(self, "premarket_plan_lock", None)
        if lock is None:
            self.premarket_plan_lock = threading.Lock()
            lock = self.premarket_plan_lock
        with lock:
            self.premarket_plan = plan
        return plan

    def premarket_plan_payload(self, refresh: bool = True) -> dict:
        if refresh:
            try:
                return _serialize_value(self._refresh_premarket_plan())
            except Exception:
                pass
        lock = getattr(self, "premarket_plan_lock", None)
        if lock is None:
            return _serialize_value(getattr(self, "premarket_plan", None) or self._empty_premarket_plan())
        with lock:
            return _serialize_value(getattr(self, "premarket_plan", None) or self._empty_premarket_plan())

    def _fresh_stock_auto_oi_confirmation(self) -> dict:
        return self._fresh_oi_confirmation({"A+ HOT", "A ACTIVE"})

    def _fresh_option_a_plus_hot_confirmation(self) -> dict:
        return self._fresh_oi_confirmation(
            {"A+ HOT"},
            max_age_seconds=settings.trading.option_auto_oi_confirmation_max_age_seconds,
        )

    def _option_signal_frame_from_fresh_oi(self) -> pd.DataFrame:
        confirmation = self._fresh_option_a_plus_hot_confirmation()
        rows = [
            dict(row.get("signal_snapshot") or {})
            for row in confirmation.get("rows") or []
            if isinstance(row.get("signal_snapshot"), dict)
            and str((row.get("signal_snapshot") or {}).get("symbol") or "").strip()
        ]
        return pd.DataFrame(rows)

    def _option_a_plus_hot_candidates(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if candidates is None or candidates.empty:
            return pd.DataFrame()
        confirmation = self._fresh_option_a_plus_hot_confirmation()
        confirmation_rows = {
            str(row.get("symbol") or "").strip().upper(): row
            for row in confirmation.get("rows") or []
            if str(row.get("symbol") or "").strip()
        }
        if not confirmation_rows:
            return pd.DataFrame(columns=[*candidates.columns, "oi_priority_label", "oi_strength_score", "oi_last_seen_at"])

        filtered = candidates[
            candidates["symbol"].astype(str).str.upper().isin(confirmation_rows)
        ].copy()
        if filtered.empty:
            return filtered
        filtered["oi_priority_label"] = filtered["symbol"].astype(str).str.upper().map(
            lambda symbol: confirmation_rows[symbol]["priority_label"]
        )
        filtered["oi_strength_score"] = filtered["symbol"].astype(str).str.upper().map(
            lambda symbol: confirmation_rows[symbol]["strength_score"]
        )
        filtered["oi_last_seen_at"] = filtered["symbol"].astype(str).str.upper().map(
            lambda symbol: confirmation_rows[symbol]["last_seen_at"]
        )
        filtered["oi_scanner_contract"] = filtered["symbol"].astype(str).str.upper().map(
            lambda symbol: confirmation_rows[symbol]["option_contract"]
        )
        filtered["oi_contract_snapshot"] = filtered["symbol"].astype(str).str.upper().map(
            lambda symbol: confirmation_rows[symbol]["contract_snapshot"]
        )
        return filtered.reset_index(drop=True)

    def _option_contract_from_a_plus_snapshot(self, row: dict) -> tuple[dict | None, str]:
        if str(row.get("oi_priority_label") or "").strip() != "A+ HOT":
            return None, "fresh A+ HOT OI confirmation is required"
        snapshot = row.get("oi_contract_snapshot")
        if not isinstance(snapshot, dict):
            return None, "A+ HOT OI contract snapshot is unavailable"
        contract = dict(snapshot)
        contract["symbol"] = self.option_client.normalize_option_symbol(contract.get("symbol"))
        bid = self._safe_float(contract.get("bid"), 0.0)
        ask = self._safe_float(contract.get("ask"), 0.0)
        mid = self._safe_float(contract.get("mid"), 0.0)
        delta = abs(self._safe_float(contract.get("delta"), 0.0))
        expected_move = self._safe_float(contract.get("expected_move"), 0.0)
        days_to_expiration = int(contract.get("days_to_expiration") or 0)
        if not contract["symbol"] or bid <= 0 or ask <= 0 or ask < bid or mid <= 0:
            return None, "A+ HOT scanner contract has invalid bid/ask pricing"
        delta_cap = self._parse_numeric_guardrail(
            self.option_bot_config.get("deltaTarget"),
            DEFAULT_OPTION_DELTA_CAP,
        )
        if delta_cap is None or delta_cap <= 0:
            delta_cap = DEFAULT_OPTION_DELTA_CAP
        if delta_cap > 1:
            delta_cap = delta_cap / 100.0
        if delta <= 0 or delta > delta_cap:
            return None, f"A+ HOT scanner contract delta {delta:.2f} exceeds the saved maximum {delta_cap:.2f}"
        if expected_move < DEFAULT_OPTION_MIN_EXPECTED_MOVE:
            return None, f"A+ HOT scanner contract expected move is below ${DEFAULT_OPTION_MIN_EXPECTED_MOVE:.2f}"
        if days_to_expiration <= 0 or days_to_expiration > OI_SCANNER_MAX_DAYS_TO_EXPIRATION:
            return None, "A+ HOT scanner contract is outside the non-0DTE 14-day expiry window"
        spread = ask - bid
        if not self._option_spread_allowed(spread, mid):
            return None, "A+ HOT scanner contract spread exceeds the configured limit"
        if contract.get("liquidity_breakout_required") and not contract.get("liquidity_breakout_passed"):
            return None, "A+ HOT scanner contract is waiting for the ATM liquidity break"
        contract["spread"] = round(spread, 4)
        contract["spread_percent"] = round((spread / mid) * 100.0, 4)
        contract["selection_source"] = "fresh_a_plus_hot_oi_snapshot"
        return contract, ""

    def _stock_auto_playbook(self) -> dict:
        session_status = self._session_status(self.client.get_clock())
        session_name = str(session_status.get("currentSession") or "Closed")
        if session_name == "Core":
            confirmation = self._fresh_stock_auto_oi_confirmation()
            overrides = {
                row["symbol"]: {
                    "liquidity_target_price": float(row.get("otm_target") or 0),
                    "liquidity_target_contract": str(row.get("option_contract") or ""),
                    "target_source": "Live OTM call liquidity strike",
                }
                for row in confirmation["rows"]
                if float(row.get("otm_target") or 0) > 0
            }
            return {
                **confirmation,
                "session": session_name,
                "mode": "core_oi_confirmed",
                "requiresFreshOi": True,
                "tradeOverrides": overrides,
                "message": "Core session requires fresh A+ HOT or A ACTIVE OI confirmation.",
            }

        if bool(session_status.get("canAutoTrade")):
            symbols = list(settings.scanner.default_universe)
            mag7_symbols = set(self._mag7_option_underlyings())
            thresholds = {
                symbol: (
                    float(settings.scanner.tos_rvol_mag7_num_dev)
                    if symbol in mag7_symbols
                    else float(settings.scanner.tos_rvol_num_dev)
                )
                for symbol in symbols
            }
            return {
                "symbols": symbols,
                "thresholds": thresholds,
                "rows": [],
                "maxAgeSeconds": 0,
                "session": session_name,
                "mode": "extended_stock_momentum",
                "requiresFreshOi": False,
                "tradeOverrides": {},
                "message": "Extended session uses the complete stock momentum setup; option flow is advisory only.",
            }

        return {
            "symbols": [],
            "thresholds": {},
            "rows": [],
            "maxAgeSeconds": 0,
            "session": session_name,
            "mode": "closed",
            "requiresFreshOi": False,
            "tradeOverrides": {},
            "message": "US stock sessions are closed. New entries will resume automatically.",
        }

    def execute_best_trade(self) -> dict:
        block_message = self._stock_account_block_message()
        if block_message:
            self.action_message = block_message
            return {
                "result": {"status": "blocked", "message": block_message},
                "dashboard": self.dashboard_payload(),
            }
        confirmation = self._stock_auto_playbook()
        if not confirmation["symbols"]:
            self.action_message = (
                "No fresh A+ HOT or A ACTIVE OI-confirmed stock setups are available."
                if confirmation.get("requiresFreshOi")
                else confirmation["message"]
            )
            self.repository.log_bot_event("stock_auto_confirmation_block", self.action_message)
            return {
                "result": {"status": "blocked", "message": self.action_message, "confirmation": confirmation},
                "dashboard": self.dashboard_payload(),
            }
        self.repository.log_bot_event(
            "stock_auto_confirmation_pass",
            f"{confirmation['mode']} approved {len(confirmation['symbols'])} stock symbol(s) for scanning.",
            json.dumps(confirmation, default=str),
        )
        response = self.trader.execute_best_candidate(
            confirmation["symbols"],
            confirmation["thresholds"],
            confirmation["tradeOverrides"],
        )
        response["confirmation"] = confirmation
        self.action_message = response["message"]
        self._sync_last_stock_scan_from_trader()
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def execute_all_trades(self, return_payload: bool = True) -> dict:
        def _result_payload(result: dict) -> dict:
            self._invalidate_dashboard_cache()
            payload = {"result": _serialize_value(result)}
            if return_payload:
                payload["dashboard"] = self.dashboard_payload()
            return payload

        block_message = self._stock_account_block_message()
        if block_message:
            self.action_message = block_message
            return _result_payload({"status": "blocked", "message": block_message})
        confirmation = self._stock_auto_playbook()
        if not confirmation["symbols"]:
            self.action_message = (
                "No fresh A+ HOT or A ACTIVE OI-confirmed stock setups are available."
                if confirmation.get("requiresFreshOi")
                else confirmation["message"]
            )
            self.repository.log_bot_event("stock_auto_confirmation_block", self.action_message)
            return _result_payload(
                {"status": "blocked", "message": self.action_message, "confirmation": confirmation}
            )
        self.repository.log_bot_event(
            "stock_auto_confirmation_pass",
            f"{confirmation['mode']} approved {len(confirmation['symbols'])} stock symbol(s) for scanning.",
            json.dumps(confirmation, default=str),
        )
        response = self.trader.execute_all_eligible_candidates(
            confirmation["symbols"],
            confirmation["thresholds"],
            confirmation["tradeOverrides"],
        )
        response["confirmation"] = confirmation
        self.action_message = response["message"]
        self._sync_last_stock_scan_from_trader()
        block_message = str(response.get("message") or "")
        if response.get("status") == "blocked":
            normalized = block_message.lower()
            if "insufficient buying power" in normalized:
                self.entry_block_status = "buying_power"
                self.entry_block_message = "No new entries: insufficient buying power. Managing existing trades only."
            elif "daily trade capital reached" in normalized:
                self.entry_block_status = "daily_cap"
                self.entry_block_message = "No new entries: daily trade capital reached. Managing existing trades only."
            elif "smaller than share price" in normalized:
                self.entry_block_status = "trade_amount"
                self.entry_block_message = "No new entries: per-trade amount too small for at least one setup."
            elif "per-trade amount is larger than the daily trade capital" in normalized:
                self.entry_block_status = "daily_cap"
                self.entry_block_message = "No new entries: per-trade amount is larger than the daily trade amount."
            else:
                self.entry_block_status = "blocked"
                self.entry_block_message = block_message or "New entries blocked."
        elif response.get("status") == "submitted":
            self.entry_block_status = ""
            self.entry_block_message = ""
        self.repository.log_bot_event(
            "auto_execution",
            self.action_message,
            json.dumps(response, default=str),
        )
        return _result_payload(response)

    def close_position(self, symbol: str) -> dict:
        response = self.trader.close_position(symbol)
        self.action_message = response["message"]
        self.repository.log_bot_event(
            "manual_close_request",
            self.action_message,
            json.dumps(response, default=str),
        )
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def close_all_positions(self) -> dict:
        response = self.trader.close_all_positions()
        self.action_message = response["message"]
        self.repository.log_bot_event(
            "manual_close_all_request",
            self.action_message,
            json.dumps(response, default=str),
        )
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def _option_trade_row_for_contract(self, option_symbol: str) -> dict:
        normalized_symbol = self.option_client.normalize_option_symbol(option_symbol)
        history = self.repository.get_option_trade_history(limit=1000, profile_id=self._option_account_profile_id())
        if history.empty:
            return {}
        frame = history.copy()
        if "option_symbol" not in frame.columns:
            return {}
        frame["_normalized_option_symbol"] = frame["option_symbol"].apply(self.option_client.normalize_option_symbol)
        frame = frame[frame["_normalized_option_symbol"] == normalized_symbol]
        if frame.empty:
            return {}
        if "opened_at" in frame.columns:
            frame["opened_at_sort"] = pd.to_datetime(frame["opened_at"], errors="coerce", utc=True)
            frame = frame.sort_values("opened_at_sort", ascending=False)
        if "status" in frame.columns:
            active_rows = frame[frame["status"].astype(str).str.lower().isin(OPTION_AUTO_ACTIVE_STATUSES)]
        else:
            active_rows = pd.DataFrame()
        source = active_rows if not active_rows.empty else frame
        return source.iloc[0].drop(labels=[column for column in ["_normalized_option_symbol", "opened_at_sort"] if column in source.columns]).to_dict()

    def _option_position_symbol_for_close(self, requested_symbol: str, position_map: dict) -> str:
        target = self.option_client.normalize_option_symbol(requested_symbol)
        if target in position_map:
            return target
        target_underlying = self._normalize_option_symbol(target)
        for option_symbol in position_map:
            if self._occ_underlying(option_symbol) == target_underlying:
                return option_symbol
        return ""

    def _close_option_position_result(self, requested_symbol: str, snapshot: dict | None = None) -> dict:
        try:
            broker_snapshot = snapshot or self._option_broker_snapshot()
        except Exception as exc:
            return {"status": "error", "message": f"Unable to reach Alpaca option account right now: {exc}"}

        position_map = broker_snapshot.get("position_map", {})
        option_symbol = self._option_position_symbol_for_close(requested_symbol, position_map)
        if not option_symbol:
            return {"status": "missing", "message": f"No open option position found for {requested_symbol}."}
        if option_symbol in broker_snapshot.get("open_sell_symbols", set()):
            return {"status": "blocked", "message": f"Sell-to-close is already pending for {option_symbol}."}

        position = position_map[option_symbol]
        quantity = abs(self._safe_float(getattr(position, "qty", None), 0.0))
        current_mid = self._safe_float(getattr(position, "current_price", None), 0.0)
        if quantity <= 0:
            return {"status": "missing", "message": f"No open quantity found for {option_symbol}."}
        if current_mid <= 0:
            current_mid = self._safe_float(getattr(position, "avg_entry_price", None), 0.0)
        if current_mid <= 0:
            return {"status": "error", "message": f"Current option mark is unavailable for {option_symbol}."}

        trade_row = self._option_trade_row_for_contract(option_symbol)
        if trade_row:
            plan = self._option_trade_plan_state(trade_row)
            if not plan:
                plan = {
                    "entry_mid": self._safe_float(trade_row.get("entry_price"), current_mid),
                    "remaining_quantity": quantity,
                    "realized_pnl": 0.0,
                }
            entry_price = self._safe_float(trade_row.get("entry_price"), plan.get("entry_mid") or current_mid)
            realized_pnl = self._safe_float(plan.get("realized_pnl"), 0.0)
            pnl = self._option_marked_pnl(entry_price, current_mid, quantity, realized_pnl)
            exit_result = self._submit_option_exit_order(
                trade_row,
                quantity,
                current_mid,
                "manual_close_requested",
                plan,
                remaining_after_fill=0.0,
                pnl=pnl,
            )
            if not exit_result.get("ok"):
                return {"status": "error", "message": exit_result.get("reason") or f"Unable to close {option_symbol}."}
            status = "submitted" if exit_result.get("journal_status") != "closed" else "closed"
            return {
                "status": status,
                "message": f"Option close submitted for {option_symbol}.",
                "symbol": option_symbol,
                "underlying": self._occ_underlying(option_symbol),
                "qty": quantity,
                "limitPrice": exit_result.get("limit_price"),
                "brokerOrderId": exit_result.get("broker_order_id"),
                "journalStatus": exit_result.get("journal_status"),
            }

        client_order_id = self._option_client_order_id(self._occ_underlying(option_symbol), prefix="option-exit")
        try:
            order = self.option_client.submit_option_limit_order(
                symbol=option_symbol,
                qty=quantity,
                limit_price=round(current_mid, 2),
                client_order_id=client_order_id,
                position_intent="sell_to_close",
            )
        except Exception as exc:
            return {"status": "error", "message": f"Alpaca option close rejected: {exc}"}

        return {
            "status": "submitted",
            "message": f"Option close submitted for {option_symbol}.",
            "symbol": option_symbol,
            "underlying": self._occ_underlying(option_symbol),
            "qty": quantity,
            "limitPrice": round(current_mid, 2),
            "brokerOrderId": str(getattr(order, "id", "") or ""),
            "journalStatus": "broker_only",
        }

    def close_option_position(self, symbol: str) -> dict:
        requested_symbol = str(symbol or "").strip().upper()
        if not requested_symbol:
            return {"result": {"status": "error", "message": "Option symbol is required."}, "dashboard": self.dashboard_payload()}
        underlying = self._occ_underlying(requested_symbol)
        profile_id = self._option_profile_for_underlying(underlying)
        response = self._run_in_option_context(
            profile_id,
            lambda: self._close_option_position_result(requested_symbol),
        )
        self.action_message = response["message"]
        self.repository.log_bot_event(
            "option_manual_close_request",
            self.action_message,
            json.dumps(response, default=str),
        )
        try:
            self._run_in_option_context(profile_id, self._sync_option_broker_state)
        except Exception:
            pass
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def close_all_option_positions(self) -> dict:
        submitted: list[dict] = []
        errors: list[dict] = []
        for profile_id in settings.option_account_profile_ids("paper"):
            def close_profile_positions():
                snapshot = self._option_broker_snapshot()
                profile_results = []
                for option_symbol in sorted(snapshot.get("position_map", {}).keys()):
                    profile_results.append(self._close_option_position_result(option_symbol, snapshot=snapshot))
                return profile_results
            try:
                results = self._run_in_option_context(profile_id, close_profile_positions)
            except Exception as exc:
                errors.append({"profileId": profile_id, "message": str(exc)})
                continue
            for result in results:
                if result.get("status") in {"submitted", "closed"}:
                    submitted.append({"profileId": profile_id, **result})
                else:
                    errors.append({"profileId": profile_id, **result})
        response = {
            "status": "submitted" if submitted else ("error" if errors else "missing"),
            "message": f"Submitted close requests for {len(submitted)} option position(s) across {len(settings.option_account_profile_ids('paper'))} accounts.",
            "submitted": submitted,
            "errors": errors,
        }

        self.action_message = response["message"]
        self.repository.log_bot_event(
            "option_manual_close_all_request",
            self.action_message,
            json.dumps(response, default=str),
        )
        try:
            self._sync_all_option_broker_states()
        except Exception:
            pass
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def cancel_stale_option_buy_orders(self) -> dict:
        response = self._cancel_stale_option_buy_orders_result()
        self.action_message = response.get("message", "Checked stale option buy orders.")
        self.repository.log_bot_event(
            "option_stale_buy_cancel_request",
            self.action_message,
            json.dumps(response, default=str),
        )
        try:
            self._sync_option_broker_state()
        except Exception:
            pass
        return {
            "result": _serialize_value(response),
            "dashboard": self.dashboard_payload(),
        }

    def update_risk_settings(
        self,
        daily_trade_amount: float | None = None,
        trade_amount: float | None = None,
        stop_loss_percent: float | None = None,
        stop_loss_amount: float | None = None,
        first_profit_target_percent: float | None = None,
    ) -> dict:
        if daily_trade_amount is not None:
            settings.trading.daily_trade_amount = max(float(daily_trade_amount), 1.0)
        if trade_amount is not None:
            settings.trading.fixed_trade_amount = max(float(trade_amount), 1.0)
        if stop_loss_percent is not None:
            settings.trading.stop_loss_percent = max(float(stop_loss_percent), 0.01)
        elif stop_loss_amount is not None:
            trade_capital = max(float(settings.trading.fixed_trade_amount), 0.01)
            settings.trading.stop_loss_percent = max((float(stop_loss_amount) / trade_capital) * 100, 0.01)

        settings.trading.stop_loss_amount = round(
            max(float(settings.trading.fixed_trade_amount), 0.01) * (max(float(settings.trading.stop_loss_percent), 0.01) / 100),
            2,
        )

        if first_profit_target_percent is not None:
            settings.trading.take_profit_1_pct = max(float(first_profit_target_percent), 0.01)

        if stop_loss_amount is not None and stop_loss_percent is not None:
            settings.trading.stop_loss_amount = max(float(stop_loss_amount), 0.01)

        self.repository.set_app_setting("daily_trade_amount", str(settings.trading.daily_trade_amount))
        self.repository.set_app_setting("trade_amount", str(settings.trading.fixed_trade_amount))
        self.repository.set_app_setting("stop_loss_percent", str(settings.trading.stop_loss_percent))
        self.repository.set_app_setting("stop_loss_amount", str(settings.trading.stop_loss_amount))
        self.repository.set_app_setting("take_profit_1_pct", str(settings.trading.take_profit_1_pct))

        self.action_message = (
            f"Risk settings updated: ${settings.trading.daily_trade_amount:.0f} daily capital, "
            f"${settings.trading.fixed_trade_amount:.0f} per trade, {settings.trading.stop_loss_percent:.2f}% stop, "
            f"{settings.trading.take_profit_1_pct:.2f}% first target."
        )
        self.repository.log_bot_event(
            "risk_settings",
            self.action_message,
            json.dumps(self._risk_settings_payload(), default=str),
        )
        return self.dashboard_payload()

    def update_scanner_storage_settings(
        self,
        history_retention_days: int | float | None = None,
    ) -> dict:
        if history_retention_days is not None:
            settings.scanner.history_retention_days = max(int(float(history_retention_days)), 1)
            self.repository.set_app_setting(
                "scanner_history_retention_days",
                str(settings.scanner.history_retention_days),
            )

        self.action_message = (
            f"Scanner history retention updated to {settings.scanner.history_retention_days} day"
            f"{'' if settings.scanner.history_retention_days == 1 else 's'}."
        )
        self.repository.log_bot_event(
            "scanner_storage_settings",
            self.action_message,
            json.dumps(self._scanner_storage_payload(), default=str),
        )
        self._invalidate_dashboard_cache()
        return self.dashboard_payload()

    def _dashboard_control_payload(self) -> dict:
        return {
            "botState": getattr(self, "bot_state", "Stopped"),
            "optionBot": {
                "state": getattr(self, "option_bot_state", "Stopped"),
                "message": getattr(self, "option_bot_message", "Option bot is idle."),
                "watchlistSource": self._option_watchlist_source(),
                "watchlistLabel": self._option_watchlist_source_label(),
                "tradeUniverseSource": self._option_watchlist_source(),
                "watchlistCount": len(self._option_bot_trade_universe()),
            },
            "actionMessage": getattr(self, "action_message", ""),
            "oiActionMessage": getattr(self, "oi_action_message", ""),
            "scanJob": dict(getattr(self, "scan_job", {"running": False, "message": "No scan running."})),
            "oiScanJob": dict(getattr(self, "oi_scan_job", {"running": False, "message": "No manual OI scan running."})),
            "oiScannerAuto": {
                "enabled": getattr(self, "oi_scanner_auto_enabled", False),
                "status": getattr(self, "oi_scanner_auto_status", "Idle"),
                "message": getattr(self, "oi_scanner_auto_message", ""),
                "lastError": getattr(self, "oi_scanner_auto_last_error", ""),
            },
            "scannerAuto": {
                "enabled": getattr(self, "scanner_auto_enabled", False),
                "status": getattr(self, "scanner_auto_status", "Idle"),
                "message": getattr(self, "scanner_auto_message", ""),
                "lastError": getattr(self, "scanner_auto_last_error", ""),
            },
        }
    def set_bot_state(self, value: str) -> dict:
        block_message = self._stock_account_block_message()
        if value == "Running" and block_message:
            self.bot_state = "Stopped"
            self.scheduler_enabled = False
            self.action_message = block_message
            self.repository.log_bot_event("bot_state_blocked", block_message)
            return self._dashboard_control_payload()
        self.bot_state = value
        stock_entry_wakeup = getattr(self, "stock_entry_wakeup", None)
        if stock_entry_wakeup is not None:
            stock_entry_wakeup.set()
        if value == "Running":
            self.scheduler_enabled = True
            self.scheduler_cycle_status = "Starting"
            self.scheduler_cycle_message = "Automation loop is starting."
            if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
                self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
                self.scheduler_thread.start()
        elif value in {"Paused", "Stopped"}:
            self.scheduler_enabled = False
            self.scheduler_cycle_status = value
            self.scheduler_cycle_message = f"Automation loop is {value.lower()}."
        self.action_message = f"{settings.account_label} bot marked as {value.lower()}."
        self.repository.log_bot_event("bot_state", self.action_message)
        self._invalidate_dashboard_cache()
        return self._dashboard_control_payload()

    def set_option_bot_state(self, value: str) -> dict:
        self.option_bot_state = value
        self.repository.set_app_setting("option_bot_state", value)
        option_scan_wakeup = getattr(self, "option_scan_wakeup", None)
        if option_scan_wakeup is not None:
            option_scan_wakeup.set()
        if value == "Running":
            self.option_bot_message = "Option bot is scanning, sending Alpaca paper option orders, and managing stops, targets, and runners."
            self._start_option_scheduler()
            self.action_message = "Option bot started the Alpaca paper option engine."
            self.repository.log_bot_event("option_bot_state", self.action_message)
            self._invalidate_dashboard_cache()
            return self._dashboard_control_payload()
        elif value == "Paused":
            self.option_bot_message = "Option bot is paused. Open Alpaca paper option positions will not be auto-managed."
        else:
            self.option_bot_message = "Option bot is stopped."
        self.action_message = f"Option bot marked as {value.lower()}."
        self.repository.log_bot_event("option_bot_state", self.action_message)
        self._invalidate_dashboard_cache()
        return self._dashboard_control_payload()

    def set_oi_scanner_auto_enabled(self, enabled: bool) -> dict:
        """Enable or stop the background, Mag7-only OI loop."""
        self.oi_scanner_auto_enabled = bool(enabled)
        self.repository.set_app_setting("oi_scanner_auto_enabled", "true" if enabled else "false")

        if enabled:
            self.oi_mag7_auto_status = "Starting"
            self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
            self.oi_scanner_auto_status = "Starting"
            self.oi_scanner_auto_message = "MAG7-only OI scanner is starting with direct Schwab/TOS option chains."
            self.oi_scanner_auto_next_run = None
            self._start_oi_scanner_auto_loops()
            self.action_message = "Background MAG7-only OI scanner enabled (direct Schwab/TOS option chains, 60-second minimum cadence)."
        else:
            self.oi_mag7_auto_status = "Stopped"
            self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
            self.oi_scanner_auto_status = "Stopped"
            self.oi_scanner_auto_message = (
                "MAG7-only OI scanner is stopped. "
                "OI Finder remains available for manual ticker searches."
            )
            self.oi_scanner_auto_next_run = None
            self.action_message = "Background MAG7-only OI scanner stopped."

        self.repository.log_bot_event("oi_scanner_auto_state", self.action_message)
        self._invalidate_dashboard_cache()
        return self._dashboard_control_payload()

    def set_stock_scanner_auto_enabled(self, enabled: bool) -> dict:
        """Enable or stop the broad background stock-watchlist scanner."""
        self.scanner_auto_enabled = bool(enabled)
        self.repository.set_app_setting("stock_scanner_auto_enabled", "true" if enabled else "false")
        if enabled:
            self.scanner_auto_status = "Starting"
            self.scanner_auto_message = "Background stock watchlist scanner is starting."
            self.scanner_auto_next_run = None
            self._start_scanner_auto_loop()
            self.action_message = "Background stock watchlist scanner enabled."
        else:
            self.scanner_auto_status = "Stopped"
            self.scanner_auto_message = "Background stock watchlist scanner is stopped."
            self.scanner_auto_next_run = None
            self.action_message = "Background stock watchlist scanner stopped."
        self.repository.log_bot_event("stock_scanner_auto_state", self.action_message)
        self._invalidate_dashboard_cache()
        return self._dashboard_control_payload()

    def update_option_bot_config(
        self,
        contract_policy: str | None = None,
        approval_mode: str | None = None,
        spread_filter: str | None = None,
        delta_target: str | None = None,
        expected_move: str | None = None,
        watchlist_source: str | None = None,
    ) -> dict:
        normalized_policy = str(contract_policy or "only_long_call").strip().lower().replace(" ", "_")
        if normalized_policy != "only_long_call":
            normalized_policy = "only_long_call"

        raw_approval = approval_mode if approval_mode is not None else self.option_bot_config.get("approvalMode", "automatic")
        normalized_approval = str(raw_approval or "automatic").strip().lower()
        if normalized_approval not in {"human", "automatic"}:
            normalized_approval = "automatic"

        raw_watchlist_source = watchlist_source if watchlist_source is not None else self.option_bot_config.get("watchlistSource", "option")
        normalized_watchlist_source = str(raw_watchlist_source or "option").strip().lower().replace("_", "-")
        if normalized_watchlist_source in {"mag7", "mag7-watchlist", "mag7-options", "mag7-watchlist-options"}:
            normalized_watchlist_source = "mag7"
        else:
            normalized_watchlist_source = "option"

        self.option_bot_config = {
            "contractPolicy": normalized_policy,
            "approvalMode": normalized_approval,
            "spreadFilter": self.option_bot_config.get("spreadFilter", "") if spread_filter is None else str(spread_filter).strip(),
            "deltaTarget": self.option_bot_config.get("deltaTarget", "") if delta_target is None else str(delta_target).strip(),
            "expectedMove": self.option_bot_config.get("expectedMove", ">=2") if expected_move is None else str(expected_move).strip(),
            "watchlistSource": normalized_watchlist_source,
        }
        self.repository.set_app_setting("option_contract_policy", self.option_bot_config["contractPolicy"])
        self.repository.set_app_setting("option_approval_mode", self.option_bot_config["approvalMode"])
        self.repository.set_app_setting("option_spread_filter", self.option_bot_config["spreadFilter"])
        self.repository.set_app_setting("option_delta_target", self.option_bot_config["deltaTarget"])
        self.repository.set_app_setting("option_expected_move", self.option_bot_config["expectedMove"])
        self.repository.set_app_setting("option_watchlist_source", self.option_bot_config["watchlistSource"])
        self.action_message = "Option bot controls updated."
        self.repository.log_bot_event(
            "option_bot_config",
            self.action_message,
            json.dumps(self.option_bot_config, default=str),
        )
        return self.dashboard_payload()

    def update_option_risk_settings(
        self,
        daily_trade_amount: str | None = None,
        trade_amount: str | None = None,
        contract_quantity: str | None = None,
        stop_loss_percent: str | None = None,
        first_profit_target_percent: str | None = None,
        first_profit_target_cons: str | None = None,
        first_profit_target_sell_mode: str | None = None,
        first_profit_target_sell_value: str | None = None,
        runner_lock_step_percent: str | None = None,
    ) -> dict:
        sell_mode = str(first_profit_target_sell_mode or "").strip().lower()
        if sell_mode not in {"percentage", "contracts"}:
            sell_mode = "percentage" if "%" in str(first_profit_target_cons or "") else "contracts"
        sell_value = "" if first_profit_target_sell_value is None else str(first_profit_target_sell_value).strip()
        composed_target_cons = "" if first_profit_target_cons is None else str(first_profit_target_cons).strip()
        if sell_value:
            composed_target_cons = f"{sell_value}%" if sell_mode == "percentage" else f"{sell_value} cons"
        updates = {
            "dailyTradeAmount": "" if daily_trade_amount is None else str(daily_trade_amount).strip(),
            "tradeAmount": "" if trade_amount is None else str(trade_amount).strip(),
            "contractQuantity": "" if contract_quantity is None else str(contract_quantity).strip(),
            "stopLossPercent": "" if stop_loss_percent is None else str(stop_loss_percent).strip(),
            "firstProfitTargetPercent": "" if first_profit_target_percent is None else str(first_profit_target_percent).strip(),
            "firstProfitTargetCons": composed_target_cons,
            "firstProfitTargetSellMode": sell_mode,
            "firstProfitTargetSellValue": sell_value,
            "runnerLockStepPercent": "" if runner_lock_step_percent is None else str(runner_lock_step_percent).strip(),
        }
        self.option_risk_settings.update(updates)
        self.repository.set_app_setting("option_daily_trade_amount", self.option_risk_settings["dailyTradeAmount"])
        self.repository.set_app_setting("option_trade_amount", self.option_risk_settings["tradeAmount"])
        self.repository.set_app_setting("option_contract_quantity", self.option_risk_settings["contractQuantity"])
        self.repository.set_app_setting("option_stop_loss_percent", self.option_risk_settings["stopLossPercent"])
        self.repository.set_app_setting("option_first_profit_target_percent", self.option_risk_settings["firstProfitTargetPercent"])
        self.repository.set_app_setting("option_first_profit_target_cons", self.option_risk_settings["firstProfitTargetCons"])
        self.repository.set_app_setting("option_first_profit_target_sell_mode", self.option_risk_settings["firstProfitTargetSellMode"])
        self.repository.set_app_setting("option_first_profit_target_sell_value", self.option_risk_settings["firstProfitTargetSellValue"])
        self.repository.set_app_setting("option_runner_lock_step_percent", self.option_risk_settings["runnerLockStepPercent"])
        self.action_message = "Option risk settings updated."
        self.repository.log_bot_event(
            "option_risk_settings",
            self.action_message,
            json.dumps(self.option_risk_settings, default=str),
        )
        return self.dashboard_payload()

    def run_backtest(
        self,
        symbols: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        start, end = self._resolve_backtest_dates(start_date, end_date)
        summary, trades = self.backtester.run(symbols, start=start, end=end)
        self.backtest_summary = summary
        self.backtest_trades = trades
        self.repository.log_backtest_run(symbols, summary)
        self.repository.update_symbol_memory_from_trades(trades)
        span = f"{start.date()} to {end.date()}"
        self.action_message = f"Backtest completed for {', '.join(symbols)} from {span}" if symbols else "Backtest completed."
        self.repository.log_bot_event("backtest", self.action_message)
        return self.dashboard_payload()

    def start_backtest_job(
        self,
        symbols: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        if self.backtest_job["running"]:
            return self.dashboard_payload()

        start, end = self._resolve_backtest_dates(start_date, end_date)
        self.backtest_job = {
            "running": True,
            "message": f"Running backtest for {', '.join(symbols)} from {start.date()} to {end.date()}",
            "symbols": symbols,
            "startDate": str(start.date()),
            "endDate": str(end.date()),
            "startedAt": datetime.now().astimezone().isoformat(),
            "finishedAt": None,
            "error": "",
        }
        thread = threading.Thread(
            target=self._run_backtest_job,
            args=(symbols, start, end),
            daemon=True,
        )
        thread.start()
        return self.dashboard_payload()

    def _run_backtest_job(self, symbols: list[str], start: datetime, end: datetime) -> None:
        try:
            summary, trades = self.backtester.run(symbols, start=start, end=end)
            self.backtest_summary = summary
            self.backtest_trades = trades
            self.repository.log_backtest_run(symbols, summary)
            self.repository.update_symbol_memory_from_trades(trades)
            total_trades = int(summary["trades"].sum()) if not summary.empty else 0
            self.action_message = f"Backtest finished: {total_trades} trades across {len(symbols)} symbols."
            self.repository.log_bot_event("backtest", self.action_message)
            self.backtest_job.update(
                {
                    "running": False,
                    "message": self.action_message,
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "error": "",
                }
            )
        except Exception as exc:
            self.action_message = f"Backtest failed: {exc}"
            self.repository.log_bot_event("backtest_error", self.action_message)
            self.backtest_job.update(
                {
                    "running": False,
                    "message": self.action_message,
                    "finishedAt": datetime.now().astimezone().isoformat(),
                    "error": str(exc),
                }
            )

    def _resolve_backtest_dates(
        self,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[datetime, datetime]:
        end = datetime.fromisoformat(end_date) if end_date else datetime.now(tz=self.backtester._tz)
        start = datetime.fromisoformat(start_date) if start_date else end - timedelta(days=365 * 6)
        if start.tzinfo is None:
            start = start.replace(tzinfo=self.backtester._tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=self.backtester._tz)
        return start, end

    def _refresh_catalyst_information(self, symbols: list[str] | None = None) -> dict:
        scoped_symbols = symbols or settings.scanner.default_universe[:40]
        items = self.catalysts.load_watchlist_news(scoped_symbols)
        self.repository.log_catalysts(items)
        message = f"Catalyst scan completed for {len(scoped_symbols)} symbols; {len(items)} headlines refreshed."
        self.repository.log_bot_event("catalyst_scan", message)
        return {
            "message": message,
            "symbolsScanned": len(scoped_symbols),
            "headlinesRefreshed": len(items),
            "refreshedAt": datetime.now().astimezone().isoformat(),
        }

    def _recent_catalysts_or_empty(self, limit: int = 200) -> pd.DataFrame:
        try:
            return self.repository.get_recent_catalysts(limit=limit)
        except Exception:
            return pd.DataFrame()

    def _latest_catalysts_or_empty(self) -> pd.DataFrame:
        try:
            return self.repository.get_latest_catalysts_by_symbol()
        except Exception:
            return pd.DataFrame()

    def _next_catalyst_information_batch(self, symbols: list[str] | None = None) -> list[str]:
        universe = _dedupe_symbol_tokens(symbols or settings.scanner.default_universe)
        if not universe:
            return []
        batch_size = min(max(int(getattr(self, "catalyst_refresh_batch_size", 40)), 1), 40, len(universe))
        cursor = int(getattr(self, "catalyst_refresh_cursor", 0)) % len(universe)
        batch = [universe[(cursor + offset) % len(universe)] for offset in range(batch_size)]
        self.catalyst_refresh_cursor = (cursor + batch_size) % len(universe)
        return batch

    def _schedule_catalyst_information_refresh(self, symbols: list[str] | None = None) -> bool:
        refresh_lock = getattr(self, "catalyst_refresh_lock", None)
        if refresh_lock is None:
            refresh_lock = threading.Lock()
            self.catalyst_refresh_lock = refresh_lock
        if not refresh_lock.acquire(blocking=False):
            return False
        batch = self._next_catalyst_information_batch(symbols)
        if not batch:
            refresh_lock.release()
            return False

        def runner() -> None:
            try:
                self._refresh_catalyst_information(batch)
            except Exception as exc:
                self.repository.log_bot_event(
                    "catalyst_scan_error",
                    f"Informational catalyst refresh failed: {exc}",
                )
            finally:
                refresh_lock.release()

        try:
            refresh_thread = threading.Thread(
                target=runner,
                name="catalyst-information-refresh",
                daemon=True,
            )
            self.catalyst_refresh_thread = refresh_thread
            refresh_thread.start()
        except Exception:
            refresh_lock.release()
            raise
        return True

    def _load_earnings_manual_imports(self) -> list[dict]:
        """Load locally reviewed screenshot imports; screenshot pixels are never retained."""
        try:
            raw = json.loads(EARNINGS_MANUAL_IMPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _persist_earnings_manual_imports(self) -> None:
        EARNINGS_MANUAL_IMPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EARNINGS_MANUAL_IMPORT_PATH.write_text(
            json.dumps(self.earnings_manual_imports, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _earnings_week_start(value: object) -> date:
        try:
            parsed = date.fromisoformat(str(value or "")[:10])
        except ValueError as exc:
            raise ValueError("Choose the Monday for the screenshot's earnings week.") from exc
        return parsed - timedelta(days=parsed.weekday())

    @staticmethod
    def _earnings_timing_code(value: object) -> str:
        token = str(value or "").strip().lower()
        aliases = {
            "bmo": "bmo",
            "before open": "bmo",
            "before market open": "bmo",
            "amc": "amc",
            "after close": "amc",
            "after market close": "amc",
            "dmh": "dmh",
            "during market hours": "dmh",
        }
        return aliases.get(token, "tbd")

    def _manual_earnings_rows(self, watchlist: tuple[str, ...], start_date: date, end_date: date) -> list[dict]:
        watchlist_set = set(watchlist)
        rows: list[dict] = []
        with self.earnings_manual_lock:
            imports = list(self.earnings_manual_imports)
        for item in imports:
            symbol = str(item.get("symbol") or "").strip().upper()
            if symbol not in watchlist_set:
                continue
            try:
                earnings_date = date.fromisoformat(str(item.get("date") or "")[:10])
            except ValueError:
                continue
            if not start_date <= earnings_date <= end_date:
                continue
            timing_code = self._earnings_timing_code(item.get("timingCode"))
            rows.append(
                {
                    "symbol": symbol,
                    "date": earnings_date.isoformat(),
                    "daysUntil": (earnings_date - start_date).days,
                    "timing": self._earnings_timing_label(timing_code),
                    "timingCode": timing_code,
                    "quarter": None,
                    "year": earnings_date.year,
                    "epsEstimate": None,
                    "epsActual": None,
                    "revenueEstimate": None,
                    "revenueActual": None,
                    "source": "Manual image import",
                    "manualImport": True,
                    "importedAt": item.get("importedAt"),
                }
            )
        return rows

    def analyze_earnings_screenshot(self, image_data: object, week_start: object) -> dict:
        """OCR a user-provided screenshot locally and return watchlist-only review rows.

        The image is written to a temporary local file solely for Windows OCR and
        removed immediately afterward.  It is never sent to Discord, Schwab, or an
        external OCR/AI provider.
        """
        image_uri = str(image_data or "").strip()
        match = re.fullmatch(r"data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=]+)", image_uri, flags=re.IGNORECASE)
        if not match:
            raise ValueError("Upload a PNG, JPG, or WebP screenshot.")
        try:
            image_bytes = base64.b64decode(match.group(2), validate=True)
        except ValueError as exc:
            raise ValueError("The screenshot could not be read.") from exc
        if not image_bytes or len(image_bytes) > EARNINGS_IMAGE_MAX_BYTES:
            raise ValueError("Use an image smaller than 8 MB.")

        week_monday = self._earnings_week_start(week_start)
        suffix = ".jpg" if match.group(1).lower() in {"jpeg", "jpg"} else f".{match.group(1).lower()}"
        try:
            with Image.open(BytesIO(image_bytes)) as source_image:
                source_image.load()
                if max(source_image.size) > 2000:
                    scale = 2000 / max(source_image.size)
                    resized = source_image.resize(
                        (max(1, round(source_image.width * scale)), max(1, round(source_image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                    output = BytesIO()
                    if resized.mode not in {"RGB", "RGBA"}:
                        resized = resized.convert("RGBA" if "transparency" in source_image.info else "RGB")
                    resized.save(output, format="PNG", optimize=True)
                    image_bytes = output.getvalue()
                    suffix = ".png"
        except Exception as exc:
            raise ValueError(f"The screenshot could not be prepared for local OCR: {type(exc).__name__}.") from exc
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="earnings-ocr-", suffix=suffix, delete=False) as handle:
                handle.write(image_bytes)
                temp_path = handle.name

            powershell_script = r'''
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asyncMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1
} | Select-Object -First 1
function Await-WinRt($operation, [Type]$resultType) {
  $task = $asyncMethod.MakeGenericMethod($resultType).Invoke($null, @($operation))
  $task.Wait()
  return $task.Result
}
$imagePath = $env:AGENTIC_EARNINGS_OCR_IMAGE
$file = Await-WinRt ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime])
$stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]::Read)) ([Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime])
$decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
$bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
$engine = [Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'Windows OCR is not available for the current user profile.' }
$result = Await-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult,Windows.Media.Ocr,ContentType=WindowsRuntime])
$words = @($result.Lines | ForEach-Object { $_.Words } | ForEach-Object {
  $rect = $_.BoundingRect
  [pscustomobject]@{ text = $_.Text; x = [double]$rect.X; y = [double]$rect.Y; width = [double]$rect.Width; height = [double]$rect.Height }
})
[pscustomobject]@{ width = [int]$bitmap.PixelWidth; height = [int]$bitmap.PixelHeight; words = $words } | ConvertTo-Json -Compress -Depth 4
'''
            env = os.environ.copy()
            env["AGENTIC_EARNINGS_OCR_IMAGE"] = temp_path
            encoded_script = base64.b64encode(powershell_script.encode("utf-16-le")).decode("ascii")
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            completed = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script],
                capture_output=True,
                text=True,
                timeout=90,
                env=env,
                check=False,
            )
            if completed.returncode != 0:
                detail_lines = str(completed.stderr or completed.stdout or "Windows OCR failed.").strip().splitlines()
                detail = detail_lines[-1] if detail_lines else "Windows OCR failed."
                raise ValueError(f"Local OCR could not read this screenshot: {detail[:180]}")
            try:
                ocr_payload = None
                for output in (str(completed.stdout or ""), str(completed.stderr or "")):
                    json_start = output.rfind('{"width"')
                    if json_start < 0:
                        continue
                    try:
                        ocr_json = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", output[json_start:].strip())
                        ocr_payload = json.loads(ocr_json)
                        break
                    except json.JSONDecodeError:
                        continue
                if not isinstance(ocr_payload, dict):
                    raise json.JSONDecodeError("OCR JSON not found", "", 0)
            except json.JSONDecodeError as exc:
                preview = re.sub(r"\s+", " ", str(completed.stdout or completed.stderr or "")).strip()
                raise ValueError(f"Local OCR returned an unreadable result: {preview[:160] or 'no output'}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Local OCR timed out. Try a smaller screenshot.") from exc
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

        image_width = max(1, int(ocr_payload.get("width") or 1))
        image_height = max(1, int(ocr_payload.get("height") or 1))
        watchlist_set = set(self._normalize_watchlist(settings.scanner.default_universe))
        candidates: dict[str, dict] = {}
        for word in ocr_payload.get("words") or []:
            if not isinstance(word, dict):
                continue
            # Weekly calendar headers such as "Before Open" can be valid ticker
            # strings (for example OPEN). Ignore the title/header band and only
            # propose rows from the body of the screenshot.
            if float(word.get("y") or 0) < image_height * 0.20:
                continue
            symbol = re.sub(r"[^A-Z0-9.]", "", str(word.get("text") or "").upper())
            if symbol not in watchlist_set:
                continue
            x_center = float(word.get("x") or 0) + (float(word.get("width") or 0) / 2)
            position = min(0.99999, max(0.0, x_center / image_width))
            day_index = min(4, int(position * 5))
            half_day_position = (position * 5) - day_index
            timing_code = "bmo" if half_day_position < 0.5 else "amc"
            earnings_date = week_monday + timedelta(days=day_index)
            key = f"{symbol}-{earnings_date.isoformat()}-{timing_code}"
            candidates[key] = {
                "key": key,
                "symbol": symbol,
                "date": earnings_date.isoformat(),
                "timingCode": timing_code,
                "timing": self._earnings_timing_label(timing_code),
                "position": {"x": round(x_center), "y": round(float(word.get("y") or 0))},
            }
        rows = sorted(candidates.values(), key=lambda item: (item["date"], item["timingCode"], item["symbol"]))
        return {
            "source": "Local Windows OCR",
            "weekStart": week_monday.isoformat(),
            "candidates": rows,
            "matchCount": len(rows),
            "message": "Review the date and timing before adding these watchlist-only entries.",
        }

    def save_manual_earnings_imports(self, rows: object) -> dict:
        if not isinstance(rows, list):
            raise ValueError("Screenshot imports must be a list of reviewed rows.")
        watchlist_set = set(self._normalize_watchlist(settings.scanner.default_universe))
        reviewed: dict[str, dict] = {}
        skipped = 0
        for item in rows[:100]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if symbol not in watchlist_set:
                skipped += 1
                continue
            try:
                earnings_date = date.fromisoformat(str(item.get("date") or "")[:10])
            except ValueError:
                skipped += 1
                continue
            timing_code = self._earnings_timing_code(item.get("timingCode"))
            reviewed[symbol] = {
                "symbol": symbol,
                "date": earnings_date.isoformat(),
                "timingCode": timing_code,
                "source": "Manual image import",
                "importedAt": datetime.now().astimezone().isoformat(),
            }
        if not reviewed:
            raise ValueError("Select at least one valid watchlist earnings row.")
        with self.earnings_manual_lock:
            existing = {
                str(item.get("symbol") or "").strip().upper(): item
                for item in self.earnings_manual_imports
                if isinstance(item, dict) and str(item.get("symbol") or "").strip().upper() not in reviewed
            }
            existing.update(reviewed)
            self.earnings_manual_imports = sorted(existing.values(), key=lambda item: (item.get("date", ""), item.get("symbol", "")))
            self._persist_earnings_manual_imports()
        self.earnings_calendar_cache = None
        return {"imported": len(reviewed), "skipped": skipped, "manualImportCount": len(self.earnings_manual_imports)}

    def clear_manual_earnings_imports(self) -> dict:
        with self.earnings_manual_lock:
            removed = len(self.earnings_manual_imports)
            self.earnings_manual_imports = []
            self._persist_earnings_manual_imports()
        self.earnings_calendar_cache = None
        return {"removed": removed, "manualImportCount": 0}

    @staticmethod
    def _earnings_timing_label(value: object) -> str:
        timing = str(value or "").strip().lower()
        return {
            "bmo": "Before market open",
            "amc": "After market close",
            "dmh": "During market hours",
        }.get(timing, "Time not confirmed")

    @staticmethod
    def _earnings_number(row: dict, *keys: str) -> float | None:
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _earnings_calendar_request(url: str) -> object:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "AgenticAI-Trading/1.0"},
        )
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _fetch_watchlist_earnings(self, start_date: date, end_date: date) -> tuple[list[dict], str, list[str]]:
        query_base = {"from": start_date.isoformat(), "to": end_date.isoformat()}
        errors: list[str] = []

        finnhub_key = str(os.getenv("FINNHUB_API_KEY", "")).strip()
        if finnhub_key:
            try:
                payload = self._earnings_calendar_request(
                    f"https://finnhub.io/api/v1/calendar/earnings?{urlencode({**query_base, 'token': finnhub_key})}"
                )
                if isinstance(payload, dict) and not payload.get("error"):
                    rows = payload.get("earningsCalendar")
                    if isinstance(rows, list):
                        return rows, "Finnhub earnings calendar", errors
                errors.append("Finnhub returned an unavailable earnings calendar response.")
            except Exception as exc:
                errors.append(f"Finnhub calendar unavailable: {type(exc).__name__}.")

        fmp_key = str(os.getenv("FMP_API_KEY", "")).strip()
        if fmp_key:
            try:
                payload = self._earnings_calendar_request(
                    f"https://financialmodelingprep.com/stable/earnings-calendar?{urlencode({**query_base, 'apikey': fmp_key})}"
                )
                if isinstance(payload, list):
                    return payload, "Financial Modeling Prep earnings calendar", []
                if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    return payload["data"], "Financial Modeling Prep earnings calendar", []
                errors.append("Financial Modeling Prep returned an unavailable earnings calendar response.")
            except Exception as exc:
                errors.append(f"Financial Modeling Prep calendar unavailable: {type(exc).__name__}.")

        if not finnhub_key and not fmp_key:
            errors.append("Set FINNHUB_API_KEY or FMP_API_KEY to load a live earnings calendar.")
        return [], "Earnings calendar unavailable", errors

    def earnings_calendar_payload(self, days: int = EARNINGS_CALENDAR_DEFAULT_DAYS, force: bool = False) -> dict:
        """Return the next scheduled earnings releases for the full saved watchlist.

        This is informational only. It deliberately uses the dedicated earnings
        providers, never Schwab, and never changes scanner or trading decisions.
        """
        horizon_days = max(7, min(int(days or EARNINGS_CALENDAR_DEFAULT_DAYS), 90))
        watchlist = tuple(self._normalize_watchlist(settings.scanner.default_universe))
        now = datetime.now().astimezone()
        with self.earnings_calendar_lock:
            cached = self.earnings_calendar_cache
            if (
                cached
                and not force
                and cached[1] == watchlist
                and (now - cached[0]).total_seconds() < EARNINGS_CALENDAR_CACHE_SECONDS
            ):
                return {**cached[2], "cached": True}

            eastern_today = pd.Timestamp.now(tz=EASTERN_TZ).date()
            end_date = eastern_today + timedelta(days=horizon_days)
            provider_rows, source, provider_errors = self._fetch_watchlist_earnings(eastern_today, end_date)
            watchlist_set = set(watchlist)
            rows_by_symbol: dict[str, dict] = {}

            for raw in provider_rows:
                if not isinstance(raw, dict):
                    continue
                symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
                if symbol not in watchlist_set:
                    continue
                raw_date = str(raw.get("date") or raw.get("earningsDate") or "").strip()
                try:
                    earnings_date = date.fromisoformat(raw_date[:10])
                except ValueError:
                    continue
                if not eastern_today <= earnings_date <= end_date:
                    continue
                timing_code = str(raw.get("hour") or raw.get("time") or "").strip().lower()
                normalized = {
                    "symbol": symbol,
                    "date": earnings_date.isoformat(),
                    "daysUntil": (earnings_date - eastern_today).days,
                    "timing": self._earnings_timing_label(timing_code),
                    "timingCode": timing_code or "tbd",
                    "quarter": raw.get("quarter") or raw.get("fiscalQuarter"),
                    "year": raw.get("year") or raw.get("fiscalYear"),
                    "epsEstimate": self._earnings_number(raw, "epsEstimate", "epsEstimated"),
                    "epsActual": self._earnings_number(raw, "epsActual", "eps"),
                    "revenueEstimate": self._earnings_number(raw, "revenueEstimate", "revenueEstimated"),
                    "revenueActual": self._earnings_number(raw, "revenueActual", "revenue"),
                    "source": source,
                    "manualImport": False,
                }
                existing = rows_by_symbol.get(symbol)
                if existing is None or normalized["date"] < existing["date"]:
                    rows_by_symbol[symbol] = normalized

            manual_rows = self._manual_earnings_rows(watchlist, eastern_today, end_date)
            for manual_row in manual_rows:
                # A screenshot import is explicitly reviewed by the user, so it wins
                # when a public provider has an older or missing report date.
                rows_by_symbol[manual_row["symbol"]] = manual_row
            rows = sorted(rows_by_symbol.values(), key=lambda item: (item["date"], item["symbol"]))
            source_label = f"{source} + manual image import" if manual_rows else source
            payload = {
                "live": bool(provider_rows) and not any("unavailable" in error.lower() for error in provider_errors),
                "source": source_label,
                "informationOnly": True,
                "watchlistCount": len(watchlist),
                "horizonDays": horizon_days,
                "startDate": eastern_today.isoformat(),
                "endDate": end_date.isoformat(),
                "refreshedAt": now.isoformat(),
                "rows": rows,
                "upcomingCount": len(rows),
                "manualImportCount": len(manual_rows),
                "errors": provider_errors,
            }
            self.earnings_calendar_cache = (now, watchlist, payload)
            return payload

    def forex_factory_us_news_payload(self, force: bool = False) -> dict:
        """Return today's and this week's medium/high impact USD events in ET.

        This is a read-only calendar context panel for the News Feed. It never
        participates in scanner scores, trade qualification, or execution.
        """
        now = pd.Timestamp.now(tz=EASTERN_TZ).to_pydatetime()
        eastern_today = now.date()
        week_start = eastern_today - timedelta(days=eastern_today.weekday())
        week_end = week_start + timedelta(days=4)
        with self.forex_factory_us_news_lock:
            cached = self.forex_factory_us_news_cache
            if (
                cached
                and not force
                and cached[1] == eastern_today
                and (now - cached[0]).total_seconds() < FOREX_FACTORY_US_NEWS_CACHE_SECONDS
            ):
                return {**cached[2], "cached": True}

            errors: list[str] = []
            raw_events: list[dict] = []
            try:
                payload = self._earnings_calendar_request(FOREX_FACTORY_CALENDAR_URL)
                if isinstance(payload, list):
                    raw_events = [item for item in payload if isinstance(item, dict)]
                else:
                    errors.append("Forex Factory returned an unavailable calendar response.")
            except Exception as exc:
                errors.append(f"Forex Factory calendar unavailable: {type(exc).__name__}.")

            events: list[dict] = []
            weekly_events: list[dict] = []
            for raw in raw_events:
                if str(raw.get("country") or "").strip().upper() != "USD":
                    continue
                impact = str(raw.get("impact") or "").strip().title()
                if impact not in {"Medium", "High"}:
                    continue
                try:
                    event_time = datetime.fromisoformat(str(raw.get("date") or "").replace("Z", "+00:00"))
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=now.tzinfo)
                    event_time = event_time.astimezone(now.tzinfo)
                except (TypeError, ValueError):
                    continue
                if not week_start <= event_time.date() <= week_end:
                    continue
                time_label = event_time.strftime("%I:%M %p").lstrip("0")
                event = {
                    "id": f"{event_time.isoformat()}-{raw.get('title')}",
                    "title": str(raw.get("title") or "USD event").strip(),
                    "time": event_time.isoformat(),
                    "timeET": time_label,
                    "date": event_time.date().isoformat(),
                    "impact": impact,
                    "forecast": str(raw.get("forecast") or "").strip(),
                    "previous": str(raw.get("previous") or "").strip(),
                    "isPast": event_time < now,
                }
                weekly_events.append(event)
                if event_time.date() == eastern_today:
                    events.append(event)
            events.sort(key=lambda item: item["time"])
            weekly_events.sort(key=lambda item: item["time"])
            payload = {
                "live": bool(raw_events) and not errors,
                "source": "Forex Factory calendar",
                "sourceUrl": "https://www.forexfactory.com/calendar",
                "informationOnly": True,
                "date": eastern_today.isoformat(),
                "dateLabel": now.strftime("%A, %b %d"),
                "weekStart": week_start.isoformat(),
                "weekEnd": week_end.isoformat(),
                "timezone": "ET",
                "updatedAt": now.isoformat(),
                "events": events,
                "weeklyEvents": weekly_events,
                "errors": errors,
            }
            self.forex_factory_us_news_cache = (now, eastern_today, payload)
            return payload

    def load_news(self, symbols: list[str] | None = None) -> dict:
        refresh = self._refresh_catalyst_information(symbols)
        self.action_message = refresh["message"]
        return {
            "actionMessage": self.action_message,
            "catalysts": _frame_records(self._recent_catalysts_or_empty(limit=200)),
            "catalystIndex": _frame_records(self._latest_catalysts_or_empty()),
            "newsFeedMeta": {
                "symbolsScanned": refresh["symbolsScanned"],
                "headlinesRefreshed": refresh["headlinesRefreshed"],
                "refreshedAt": refresh["refreshedAt"],
            },
        }

    def chart_payload(self, symbol: str, timeframe: str = "5Min") -> dict:
        target = (symbol or "").strip().upper() or "AAPL"
        timeframe = "5Min"
        if hasattr(self.market_data_client, "ensure_streaming"):
            self.market_data_client.ensure_streaming([target, "SPY"])
        signal_frame = self.market_data_client.get_chart_bars(target, timeframe=timeframe, days_back=20)
        frame = signal_frame
        mtf_payload = _tos_mtf_ema_signal_payload(signal_frame)
        bars = []
        if not frame.empty:
            bars = [
                {
                    "time": int(bar["timestamp"].timestamp()),
                    "open": round(float(bar["open"]), 2),
                    "high": round(float(bar["high"]), 2),
                    "low": round(float(bar["low"]), 2),
                    "close": round(float(bar["close"]), 2),
                    "volume": int(bar["volume"]),
                }
                for _, bar in frame.tail(240).iterrows()
            ]
        first_chart_time = bars[0]["time"] if bars else 0
        visible_signals = [
            signal
            for signal in mtf_payload["signals"]
            if int(signal.get("time") or 0) >= first_chart_time
        ]
        return {
            "symbol": target,
            "timeframe": timeframe,
            "source": "Schwab/TOS API" if settings.market_data_provider == "schwab" else "Alpaca Market Data",
            "bars": bars,
            "error": "" if bars else f"No 5-minute candles were returned for {target}. Schwab market data may need to reconnect.",
            "mtfSignals": visible_signals,
            "mtfSignalStates": mtf_payload["states"],
            "mtfSignalMode": mtf_payload["mode"],
            "updatedAt": datetime.now().astimezone().isoformat(),
        }

    def _owner_alpaca_chart_client(self):
        """Alpaca data client from the owner's saved API key (Settings page).

        The env-profile Alpaca credentials are stale; the user maintains their
        working key through Settings -> API credentials, which lands in the
        encrypted per-user store. Cache: None = unresolved, False = known
        unavailable, client object = ready.
        """
        cached = getattr(self, "_owner_alpaca_client_cache", None)
        if cached is not None:
            return cached or None
        client = None
        try:
            connection = sqlite3.connect(str(DATABASE_PATH), timeout=10.0)
            try:
                row = connection.execute(
                    "SELECT id FROM app_users WHERE is_active = 1 "
                    "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1"
                ).fetchone()
            finally:
                connection.close()
            if row:
                credentials = auth_service_instance().get_provider_credentials(
                    str(row[0]), "alpaca_market_data",
                )
                key = str(credentials.get("key_id") or "").strip()
                secret = str(credentials.get("secret_key") or "").strip()
                if key and secret:
                    from alpaca.data.historical import StockHistoricalDataClient
                    client = StockHistoricalDataClient(api_key=key, secret_key=secret)
        except Exception:
            client = None
        self._owner_alpaca_client_cache = client if client is not None else False
        return client

    def _alpaca_fallback_chart_bars(self, symbol: str, timeframe: str, days_back: int) -> pd.DataFrame:
        """Chart candles from the owner's Alpaca key when Schwab has none."""
        client = self._owner_alpaca_chart_client()
        if client is None:
            return pd.DataFrame()
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            timeframe_key = str(timeframe).lower()
            if timeframe_key.startswith("1day") or timeframe_key in {"1d", "d", "day"}:
                bar_timeframe = TimeFrame(1, TimeFrameUnit.Day)
            else:
                bar_timeframe = TimeFrame(5 if timeframe_key.startswith("5") else 1, TimeFrameUnit.Minute)
            try:
                feed = DataFeed(settings.alpaca_data_feed)
            except ValueError:
                feed = DataFeed.IEX
            request = StockBarsRequest(
                symbol_or_symbols=str(symbol).upper(),
                timeframe=bar_timeframe,
                # Never a shorter window than five calendar days: a "2 day"
                # fast-start request issued on a weekend or Monday pre-market
                # would otherwise span zero trading sessions and blank the
                # chart even though the key works.
                start=datetime.now(timezone.utc) - timedelta(days=max(5, int(days_back))),
                feed=feed,
            )
            response = client.get_stock_bars(request)
            frame = getattr(response, "df", None)
            if frame is None or frame.empty:
                return pd.DataFrame()
            frame = frame.reset_index()
            if "symbol" in frame.columns:
                frame = frame[frame["symbol"].astype(str).str.upper() == str(symbol).upper()]
            columns = ["timestamp", "open", "high", "low", "close", "volume"]
            if any(column not in frame.columns for column in columns):
                return pd.DataFrame()
            return frame[columns].sort_values("timestamp").reset_index(drop=True)
        except Exception:
            return pd.DataFrame()

    @staticmethod
    def _four_hour_archive_frame(
        daily_frame: pd.DataFrame | None,
        intraday_frame: pd.DataFrame | None,
    ) -> tuple[pd.DataFrame, dict]:
        """Join truthful long daily OHLC with the broker's exact intraday tape.

        Schwab caps minute-frequency history near nine months. Older daily
        candles remain daily-resolution archive points; they are not split or
        interpolated into fictional intraday candles. The browser receives the
        coverage boundary so this mixed-resolution history is explicit.
        """
        columns = ["timestamp", "open", "high", "low", "close", "volume"]

        def normalized(source: pd.DataFrame | None) -> pd.DataFrame:
            if not isinstance(source, pd.DataFrame) or source.empty:
                return pd.DataFrame(columns=columns)
            if any(column not in source.columns for column in columns):
                return pd.DataFrame(columns=columns)
            result = source[columns].copy()
            result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True).dt.tz_convert(EASTERN_TZ)
            return result.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        daily = normalized(daily_frame)
        intraday = normalized(intraday_frame)
        exact_from = None
        archive = daily
        if not intraday.empty:
            exact_stamp = pd.Timestamp(intraday.iloc[0]["timestamp"])
            exact_from = exact_stamp.isoformat()
            exact_date = exact_stamp.tz_convert(EASTERN_TZ).date()
            if not daily.empty:
                archive_dates = pd.to_datetime(daily["timestamp"], utc=True).dt.tz_convert(EASTERN_TZ).dt.date
                archive = daily.loc[archive_dates < exact_date]

        combined = pd.concat([archive, intraday], ignore_index=True)
        if not combined.empty:
            combined = (
                combined
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        first_stamp = pd.Timestamp(combined.iloc[0]["timestamp"]).isoformat() if not combined.empty else None
        last_stamp = pd.Timestamp(combined.iloc[-1]["timestamp"]).isoformat() if not combined.empty else None
        return combined, {
            "mode": (
                "daily-archive+exact-30m"
                if not archive.empty and not intraday.empty
                else "exact-30m" if not intraday.empty else "daily-archive"
            ),
            "archiveFrom": first_stamp,
            "exactFrom": exact_from,
            "through": last_stamp,
            "requestedYears": 20,
        }

    def _build_oi_finder_chart_payload(self, symbol: str, fast_start: bool = False) -> dict:
        """Build recent candles first; long study history is optional."""
        target = (symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            return {
                "symbol": target,
                "timeframe": "1Min",
                "source": "Schwab/TOS API",
                "live": False,
                "bars": [],
                "dailyBars": [],
                "ganeshHigherTimeframeSignals": {
                    "schemaVersion": GANESH_SCHEMA_VERSION,
                    "mode": GANESH_SIGNAL_MODE,
                    "sourceAggregationMinutes": GANESH_SOURCE_AGGREGATION_MINUTES,
                    "historyReady": False,
                    "signals": [],
                },
                "error": "Enter a valid ticker symbol.",
                "updatedAt": datetime.now().astimezone().isoformat(),
            }

        try:
            schwab_client = SchwabClient()
            chart_source = "Schwab/TOS API"
            schwab_chart_error = ""
            frame = pd.DataFrame()
            # One-minute source bars let the browser form exact 3m/5m/10m/
            # 15m/30m/1h/2h/4h candles from the same Schwab/TOS stream. This
            # remains one request for the ticker currently open in OI Finder.
            if schwab_client.configured:
                try:
                    # Thirty calendar days of one-minute bars matches MomoX's
                    # 4h 30D composition (~90+ four-hour candles). Fast first
                    # paint still comes from the 2-day fast_start build; this
                    # deep tape lands via the background refresh and recency
                    # refreshes splice onto it instead of replacing it.
                    frame = schwab_client.get_chart_bars(
                        target,
                        timeframe="1Min",
                        days_back=2 if fast_start else 30,
                    )
                except Exception as exc:
                    schwab_chart_error = str(exc)
            else:
                schwab_chart_error = "Schwab/TOS is not connected."
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                # Expired/missing Schwab tokens must not blank the charts:
                # fall back to the owner's Alpaca key, mirroring the option
                # chain's Tradier fallback.
                fallback = self._alpaca_fallback_chart_bars(target, "1Min", 2 if fast_start else 7)
                if isinstance(fallback, pd.DataFrame) and not fallback.empty:
                    frame = fallback
                    chart_source = "Alpaca candles (Schwab/TOS fallback)"
            # The 4-hour 9×20 EMA needs materially more than the seven days
            # of one-minute candles used by the visible chart.  Keep a longer
            # 5-minute history exclusively for the supplied MTF studies, and
            # cache it so a live-chart refresh does not make another history
            # request every two seconds.
            now_et = datetime.now(tz=ZoneInfo(EASTERN_TZ))
            # Bind the shared cache on first use. This used to be a bare
            # getattr default, so every build before the first successful
            # full-history pass wrote into a throwaway dict that was dropped
            # on return — the deep tapes were re-fetched from the broker over
            # and over instead of being reused.
            history_cache = getattr(self, "_oi_finder_mtf_history_cache", None)
            if not isinstance(history_cache, dict):
                history_cache = {}
                self._oi_finder_mtf_history_cache = history_cache
            cached_history = history_cache.get(target, {}) if isinstance(history_cache, dict) else {}
            cached_at = cached_history.get("cached_at")
            signal_frame = cached_history.get("frame")
            daily_frame = cached_history.get("daily_frame")
            study_frame = cached_history.get("study_frame")

            def history_frame_span_days(candidate: object) -> float:
                if not isinstance(candidate, pd.DataFrame) or candidate.empty or "timestamp" not in candidate:
                    return 0.0
                timestamps = pd.to_datetime(candidate["timestamp"], errors="coerce", utc=True).dropna()
                if len(timestamps) < 2:
                    return 0.0
                return max(0.0, (timestamps.max() - timestamps.min()).total_seconds() / 86_400)

            cache_has_timeframe_depth = (
                isinstance(daily_frame, pd.DataFrame)
                and len(daily_frame) >= 20
                and isinstance(study_frame, pd.DataFrame)
                and len(study_frame) >= 20
                and history_frame_span_days(daily_frame) >= 20
                and history_frame_span_days(study_frame) >= 20
            )
            cache_is_fresh = (
                isinstance(cached_at, datetime)
                and isinstance(signal_frame, pd.DataFrame)
                and not signal_frame.empty
                and isinstance(daily_frame, pd.DataFrame)
                and not daily_frame.empty
                and (now_et - cached_at).total_seconds() < 300
                and cache_has_timeframe_depth
            )
            # Keep a shallow response paintable, but do not hammer the deep
            # broker endpoints on every sub-second readiness poll.
            shallow_retry_deferred = (
                isinstance(cached_at, datetime)
                and not cache_has_timeframe_depth
                and (now_et - cached_at).total_seconds() < 15
            )
            # Keep a cold first paint to one small broker request. Deep daily
            # and 30-minute tapes arrive from disk immediately when available,
            # otherwise the single-lane background promotion fills them. Doing
            # both deep requests here regressed chart-ready time by seconds.
            fast_study_seed = None
            if not cache_is_fresh and not fast_start and not shallow_retry_deferred:
                try:
                    long_history = pd.DataFrame()
                    if schwab_client.configured and chart_source == "Schwab/TOS API":
                        long_history = schwab_client.get_chart_bars(target, timeframe="5Min", days_back=60)
                    if not isinstance(long_history, pd.DataFrame) or long_history.empty:
                        long_history = self._alpaca_fallback_chart_bars(target, "5Min", 60)
                    if isinstance(long_history, pd.DataFrame) and not long_history.empty:
                        signal_frame = long_history
                        # Deep daily history drives the Daily/Weekly/Monthly
                        # panes - MomoX-depth (~10 years) straight from Schwab
                        # daily bars, Alpaca daily as fallback, and only then
                        # the 60-day 5-minute aggregation as a last resort.
                        deep_daily = pd.DataFrame()
                        if chart_source == "Schwab/TOS API":
                            try:
                                deep_daily = schwab_client.get_chart_bars(target, timeframe="1Day", days_back=7300)
                            except Exception:
                                deep_daily = pd.DataFrame()
                        if not isinstance(deep_daily, pd.DataFrame) or deep_daily.empty:
                            deep_daily = self._alpaca_fallback_chart_bars(target, "1Day", 7300)
                        daily_source = long_history.copy()
                        daily_source["_date"] = (
                            pd.to_datetime(daily_source["timestamp"], utc=True)
                            .dt.tz_convert(EASTERN_TZ)
                            .dt.date
                        )
                        daily_frame = (
                            daily_source
                            .sort_values("timestamp")
                            .groupby("_date", as_index=False)
                            .agg(
                                timestamp=("timestamp", "first"),
                                open=("open", "first"),
                                high=("high", "max"),
                                low=("low", "min"),
                                close=("close", "last"),
                                volume=("volume", "sum"),
                            )
                        )
                        if isinstance(deep_daily, pd.DataFrame) and not deep_daily.empty:
                            daily_frame = deep_daily.sort_values("timestamp").reset_index(drop=True)
                        # Deep 30-minute tape feeds the 4H pane (the frontend
                        # aggregates studyBars for the 240-minute view). The
                        # 30-day 1-minute tape alone gave 4H only ~96 candles
                        # ("I see only up to July"); 30-min bars reach months
                        # back. Falls back to the 60-day 5-minute frame.
                        deep_study = pd.DataFrame()
                        if chart_source == "Schwab/TOS API":
                            try:
                                deep_study = schwab_client.get_chart_bars(
                                    target,
                                    timeframe="30Min",
                                    days_back=OI_FINDER_CHART_INTRADAY_LOOKBACK_DAYS,
                                )
                            except Exception:
                                deep_study = pd.DataFrame()
                        if not isinstance(deep_study, pd.DataFrame) or deep_study.empty:
                            deep_study = self._alpaca_fallback_chart_bars(
                                target,
                                "30Min",
                                OI_FINDER_CHART_INTRADAY_LOOKBACK_DAYS,
                            )
                        study_frame = (
                            deep_study
                            if isinstance(deep_study, pd.DataFrame) and not deep_study.empty
                            else long_history
                        )
                        history_cache[target] = {
                            "cached_at": now_et,
                            "frame": long_history,
                            "daily_frame": daily_frame,
                            "study_frame": study_frame,
                            # Preserve the deepest 30-minute tape seen for this
                            # symbol so 4H keeps its full depth even when this
                            # pass's own 30-minute request came back empty.
                            "deep_study_frame": (
                                deep_study
                                if isinstance(deep_study, pd.DataFrame) and not deep_study.empty
                                else (cached_history.get("deep_study_frame"))
                            ),
                        }
                        # Retain only the currently used small history cache.
                        # Cap must exceed the quick-strip + active-panel symbol
                        # count: a 12-entry cap under 13+ live tickers caused
                        # permanent eviction thrash and 98% CPU re-fetch loops.
                        if len(history_cache) > 48:
                            # Trim to the cap, not below it: [:-12] emptied the
                            # cache to 12 entries and re-created the eviction
                            # thrash the 48 cap exists to prevent.
                            oldest = sorted(
                                history_cache,
                                key=lambda key: history_cache[key].get("cached_at", now_et),
                            )[:-48]
                            for key in oldest:
                                history_cache.pop(key, None)
                        self._oi_finder_mtf_history_cache = history_cache
                except Exception:
                    # The visible one-minute data remains usable if the longer
                    # study-history request is temporarily unavailable.
                    signal_frame = frame
            if not isinstance(daily_frame, pd.DataFrame) or daily_frame.empty:
                daily_source = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
                if not daily_source.empty:
                    daily_source["_date"] = (
                        pd.to_datetime(daily_source["timestamp"], utc=True)
                        .dt.tz_convert(EASTERN_TZ)
                        .dt.date
                    )
                    daily_frame = (
                        daily_source
                        .sort_values("timestamp")
                        .groupby("_date", as_index=False)
                        .agg(
                            timestamp=("timestamp", "first"),
                            open=("open", "first"),
                            high=("high", "max"),
                            low=("low", "min"),
                            close=("close", "last"),
                            volume=("volume", "sum"),
                        )
                    )
            if not isinstance(study_frame, pd.DataFrame) or study_frame.empty:
                study_frame = signal_frame if isinstance(signal_frame, pd.DataFrame) and not signal_frame.empty else frame
            mtf_payload = _tos_mtf_ema_signal_payload(study_frame)
            watchlist_mtf_payload = _tos_watchlist_mtf_signal_payload(study_frame)
            # Column-wise zip serialization: iterrows() on a 13k-row frame
            # burned seconds of CPU per build; zip keeps it in milliseconds.
            bars_tail = frame.tail(28000) if frame is not None and not frame.empty else None
            bars = [
                {
                    "time": int(ts.timestamp()),
                    "open": round(float(open_), 2),
                    "high": round(float(high), 2),
                    "low": round(float(low), 2),
                    "close": round(float(close), 2),
                    "volume": int(volume),
                }
                for ts, open_, high, low, close, volume in zip(
                    bars_tail["timestamp"], bars_tail["open"], bars_tail["high"],
                    bars_tail["low"], bars_tail["close"], bars_tail["volume"],
                )
            ] if bars_tail is not None else []
            daily_tail = daily_frame.tail(5500) if isinstance(daily_frame, pd.DataFrame) and not daily_frame.empty else None
            daily_bars = [
                {
                    "time": int(ts.timestamp()),
                    "date": ts.astimezone(ZoneInfo(EASTERN_TZ)).date().isoformat(),
                    "open": round(float(open_), 4),
                    "high": round(float(high), 4),
                    "low": round(float(low), 4),
                    "close": round(float(close), 4),
                    "volume": int(volume),
                }
                for ts, open_, high, low, close, volume in zip(
                    daily_tail["timestamp"], daily_tail["open"], daily_tail["high"],
                    daily_tail["low"], daily_tail["close"], daily_tail["volume"],
                )
            ] if daily_tail is not None else []
            # 4H aggregates this tape. Schwab supplies exact 30-minute bars for
            # only its current intraday retention window, so prepend truthful
            # daily OHLC archive points for the older years. This yields full
            # twenty-year pan depth while preserving exact 4H candles wherever
            # genuine intraday data exists.
            cached_deep_study = cached_history.get("deep_study_frame")
            study_series_frame = study_frame
            for candidate in (fast_study_seed, cached_deep_study):
                if isinstance(candidate, pd.DataFrame) and not candidate.empty:
                    study_series_frame = candidate
                    break
            study_tail, four_hour_coverage = self._four_hour_archive_frame(
                daily_frame,
                study_series_frame,
            )
            study_bars = [
                {
                    "time": int(ts.timestamp()),
                    "open": round(float(open_), 4),
                    "high": round(float(high), 4),
                    "low": round(float(low), 4),
                    "close": round(float(close), 4),
                    "volume": int(volume),
                }
                for ts, open_, high, low, close, volume in zip(
                    study_tail["timestamp"], study_tail["open"], study_tail["high"],
                    study_tail["low"], study_tail["close"], study_tail["volume"],
                )
            ] if not study_tail.empty else []
            first_chart_time = bars[0]["time"] if bars else 0
            visible_signals = [
                signal
                for signal in mtf_payload["signals"]
                if int(signal.get("time") or 0) >= first_chart_time
            ]
            # A full history build is complete only when the minute tape spans
            # multiple sessions. A transient provider hiccup that returns a
            # single day must keep historyLoading=True so the refresh loop
            # retries the full fetch instead of freezing intraday panes thin.
            minute_days = len({
                datetime.fromtimestamp(bar["time"], tz=ZoneInfo(EASTERN_TZ)).date()
                for bar in bars
            }) if bars else 0
            history_complete = (
                (not fast_start)
                and minute_days >= 2
                and self._chart_payload_has_multi_timeframe_depth({
                    "dailyBars": daily_bars,
                    "studyBars": study_bars,
                })
            )
            return {
                "symbol": target,
                "timeframe": "1Min",
                "source": chart_source,
                "live": bool(bars),
                "bars": bars,
                "studyBars": study_bars,
                "dailyBars": daily_bars,
                "fourHourCoverage": four_hour_coverage,
                "mtfSignals": visible_signals,
                "mtfSignalStates": mtf_payload["states"],
                "mtfSignalMode": mtf_payload["mode"],
                "watchlistMtfStates": watchlist_mtf_payload["states"],
                # The chart's Ganesh D/2D/3D/4D/W/M studies read this key. The
                # rebuilt backend never emitted it, so those three indicators
                # rendered nothing however they were toggled. Shares the same
                # study-tape-keyed memo as the MAG7 scanner tables, so having
                # both on costs one replay per tape, not two.
                "ganeshHigherTimeframeSignals": self._ganesh_signal_payload_for_chart(
                    study_bars, bars, daily_bars, target,
                    # Window to the visible tape exactly like mtfSignals above.
                    # Unwindowed this shipped the entire multi-year replay —
                    # 6,125 signals per payload, per panel, every poll — which
                    # the browser then had to project onto the chart. That is
                    # what made the app crawl once the studies were re-wired.
                    first_chart_time=first_chart_time,
                ),
                "historyLoading": not history_complete,
                "error": "" if bars else (
                    f"No one-minute candles were returned for {target}. "
                    f"Schwab/TOS: {schwab_chart_error or 'no data'}; Alpaca fallback "
                    "needs a working key saved in Settings."
                ),
                "updatedAt": datetime.now().astimezone().isoformat(),
            }
        except Exception as exc:
            return {
                "symbol": target,
                "timeframe": "1Min",
                "source": "Schwab/TOS API",
                "live": False,
                "bars": [],
                "dailyBars": [],
                "ganeshHigherTimeframeSignals": {
                    "schemaVersion": GANESH_SCHEMA_VERSION,
                    "mode": GANESH_SIGNAL_MODE,
                    "sourceAggregationMinutes": GANESH_SOURCE_AGGREGATION_MINUTES,
                    "historyReady": False,
                    "signals": [],
                },
                "error": str(exc),
                "updatedAt": datetime.now().astimezone().isoformat(),
            }

    def _oi_finder_chart_disk_path(self, symbol: str) -> Path | None:
        target = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            return None
        return Path(self.oi_finder_chart_disk_cache_dir) / f"{target}.json.gz"

    def _load_oi_finder_chart_disk_payload(self, symbol: str) -> dict | None:
        path = self._oi_finder_chart_disk_path(symbol)
        if path is None:
            return None
        try:
            age_seconds = max(0.0, time.time() - path.stat().st_mtime)
            if age_seconds > OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS:
                return None
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or not payload.get("bars"):
                return None
            return {
                **payload,
                "diskCached": True,
                "diskCacheAgeSeconds": round(age_seconds, 2),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def _save_oi_finder_chart_disk_payload(self, symbol: str, payload: dict) -> None:
        if (
            not isinstance(payload, dict)
            or not payload.get("bars")
            or bool(payload.get("historyLoading"))
        ):
            return
        path = self._oi_finder_chart_disk_path(symbol)
        if path is None:
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
                json.dump(payload, handle, separators=(",", ":"), default=str)
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _chart_payload_has_twenty_year_four_hour_archive(payload: dict | None) -> bool:
        coverage = (payload or {}).get("fourHourCoverage")
        return bool(
            isinstance(coverage, dict)
            and int(coverage.get("requestedYears") or 0) >= 20
            and (payload or {}).get("studyBars")
        )

    @staticmethod
    def _chart_payload_has_multi_timeframe_depth(payload: dict | None) -> bool:
        """Reject a one-session seed as completed 4H/D/W/M history."""
        source = payload or {}
        daily = source.get("dailyBars") or []
        study = source.get("studyBars") or []
        if len(daily) < 20 or len(study) < 20:
            return False
        try:
            daily_span = int(daily[-1].get("time") or 0) - int(daily[0].get("time") or 0)
            study_span = int(study[-1].get("time") or 0) - int(study[0].get("time") or 0)
        except (AttributeError, TypeError, ValueError):
            return False
        minimum_span = 20 * 86_400
        return daily_span >= minimum_span and study_span >= minimum_span

    @staticmethod
    def _chart_payload_has_ready_ganesh_signals(payload: dict | None) -> bool:
        contract = (payload or {}).get("ganeshHigherTimeframeSignals")
        return bool(
            isinstance(contract, dict)
            and contract.get("schemaVersion") == GANESH_SCHEMA_VERSION
            and contract.get("mode") == GANESH_SIGNAL_MODE
            and int(contract.get("sourceAggregationMinutes") or 0)
            == GANESH_SOURCE_AGGREGATION_MINUTES
            and contract.get("historyReady") is True
            and isinstance(contract.get("signals"), list)
        )

    def _upgrade_cached_ganesh_signal_tape(self, target: str) -> dict | None:
        """Rebuild a missing/stale D..M tape from already-persisted bars.

        Several current disk caches contain the complete 20-year 4H source
        and daily seed but predate the versioned Ganesh response contract.
        They are fully usable chart histories, so refetching years of broker
        data is unnecessary. Replay only the three signal studies locally and
        return an upgraded payload for the normal atomic cache-save path.
        """
        with self.oi_finder_chart_lock:
            cached = self.oi_finder_chart_cache.get(target)
            payload = dict(cached.get("payload") or {}) if cached else None
        if not payload or self._chart_payload_has_ready_ganesh_signals(payload):
            return None
        if not self._chart_payload_has_twenty_year_four_hour_archive(payload):
            return None
        bars = payload.get("bars") or []
        study_bars = payload.get("studyBars") or []
        daily_bars = payload.get("dailyBars") or []
        if not bars or not study_bars or not daily_bars:
            return None
        newest_bar_time = int((bars[-1] or {}).get("time") or 0)
        if not newest_bar_time or max(0.0, time.time() - newest_bar_time) > OI_FINDER_CHART_STALE_TAPE_SECONDS:
            return None
        signal_payload = self._ganesh_signal_payload_for_chart(
            study_bars,
            bars,
            daily_bars,
            target,
        )
        if signal_payload.get("historyReady") is not True:
            return None
        payload["ganeshHigherTimeframeSignals"] = signal_payload
        payload["studySchemaStale"] = False
        payload["historyLoading"] = False
        return payload

    def _refresh_oi_finder_chart_payload(self, target: str, full_history: bool) -> None:
        try:
            full_refresh_lock = getattr(self, "oi_finder_chart_full_refresh_lock", None)
            if full_history and full_refresh_lock is not None:
                # Indicator replay is CPU-heavy Python work. One lane prevents
                # several chart panels from freezing the browser/backend at
                # once while still allowing lightweight recency refreshes.
                with full_refresh_lock:
                    payload = self._upgrade_cached_ganesh_signal_tape(target)
                    if payload is None:
                        payload = self._build_oi_finder_chart_payload(target, fast_start=False)
            else:
                payload = self._build_oi_finder_chart_payload(
                    target,
                    fast_start=not full_history,
                )
            if payload.get("bars"):
                with self.oi_finder_chart_lock:
                    cached = self.oi_finder_chart_cache.get(target)
                    if not full_history and cached and cached.get("history_ready"):
                        # A recency refresh fetches only ~2 days of bars. Splice
                        # them onto the cached deep tape instead of replacing it,
                        # so the 1h/2h/4h panes keep their weeks of candles and
                        # the D/W/M panes keep their years between full builds.
                        previous = cached.get("payload") or {}
                        old_bars = previous.get("bars") or []
                        new_bars = payload.get("bars") or []
                        if old_bars and new_bars:
                            first_new_time = new_bars[0]["time"]
                            payload["bars"] = [
                                bar for bar in old_bars if bar["time"] < first_new_time
                            ] + new_bars
                        old_study = previous.get("studyBars") or []
                        if len(old_study) > len(payload.get("studyBars") or []):
                            # The deep 30-min tape only ships with full builds;
                            # never let a recency refresh erase it.
                            payload["studyBars"] = old_study
                        old_daily = previous.get("dailyBars") or []
                        if len(old_daily) > len(payload.get("dailyBars") or []):
                            merged_daily = {bar["time"]: bar for bar in old_daily}
                            for bar in payload.get("dailyBars") or []:
                                merged_daily[bar["time"]] = bar
                            payload["dailyBars"] = [merged_daily[key] for key in sorted(merged_daily)]
                        payload["historyLoading"] = False
                    self.oi_finder_chart_cache[target] = {
                        "cached_at": time.monotonic(),
                        "payload": payload,
                        "history_ready": not bool(payload.get("historyLoading")),
                    }
                    if len(self.oi_finder_chart_cache) > OI_FINDER_CHART_WARM_LIMIT:
                        oldest = sorted(
                            self.oi_finder_chart_cache,
                            key=lambda key: self.oi_finder_chart_cache[key].get("cached_at", 0),
                        )[:-OI_FINDER_CHART_WARM_LIMIT]
                        for key in oldest:
                            self.oi_finder_chart_cache.pop(key, None)
                if full_history and not payload.get("historyLoading"):
                    self._save_oi_finder_chart_disk_payload(target, payload)
        finally:
            with self.oi_finder_chart_lock:
                self.oi_finder_chart_refreshes.discard(target)

    def _start_oi_finder_chart_refresh(self, target: str, full_history: bool) -> None:
        with self.oi_finder_chart_lock:
            if target in self.oi_finder_chart_refreshes:
                return
            self.oi_finder_chart_refreshes.add(target)
        def refresh() -> None:
            # Yield long enough for the initial JSON response to leave the
            # request thread before provider parsing/study replay begins.
            if full_history:
                time.sleep(OI_FINDER_CHART_FULL_REFRESH_DEFER_SECONDS)
            self._refresh_oi_finder_chart_payload(target, full_history)

        threading.Thread(target=refresh, name=f"oi-finder-chart-{target}", daemon=True).start()

    # Browser transport contract (restored 2026-08-10 after the rebuild; the
    # 28k-line frontend already speaks it):
    # - historyStatus=true polls at 450-900ms during a ticker switch and must
    #   get a tiny readiness object, never a tape.
    # - initial=true is the first paint: recent bars + daily seed only.
    # - since=<epoch> is the 30s reconcile: tail + scalars, a few KB.
    # Without these, every poll shipped the full ~27k-bar deep tape.
    OI_CHART_INITIAL_BAR_COUNT = 900
    OI_CHART_INITIAL_DAILY_BAR_COUNT = 320
    # A 4H opening viewport needs roughly 120 candles.  The deep study tape is
    # 30-minute data with a daily archive prepended, so 1,600 fine bars provide
    # ample 4H context while remaining a small first-paint response.
    OI_CHART_INITIAL_STUDY_BAR_COUNT = 1_600
    OI_CHART_DELTA_OVERLAP_SECONDS = 180.0
    OI_CHART_INITIAL_SLIM_KEYS = (
        "studyBars",
        "ganeshHigherTimeframeSignals",
        "mtfSignals",
        "mtfSignalStates",
        "mtfLiveSignalContexts",
        "watchlistMtfStates",
    )

    def touch_oi_finder_interactive_window(self) -> None:
        """Any trader-facing payload request pauses background scanning 45s."""
        self.oi_finder_interactive_until = max(
            float(getattr(self, "oi_finder_interactive_until", 0.0)),
            time.monotonic() + 45.0,
        )

    def oi_finder_chart_history_status(self, symbol: str) -> dict:
        """Tiny readiness response so ticker-switch polls do not re-ship tapes."""
        target = (symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            return {"symbol": target, "historyReady": False, "refreshing": False}
        with self.oi_finder_chart_lock:
            cached = self.oi_finder_chart_cache.get(target)
            refreshing = target in self.oi_finder_chart_refreshes
        cached_payload = cached.get("payload") if cached else None
        history_ready = bool(
            cached
            and cached.get("history_ready")
            and self._chart_payload_has_multi_timeframe_depth(cached_payload)
            and self._chart_payload_has_ready_ganesh_signals(cached_payload)
        )
        has_bars = bool(cached and cached.get("payload", {}).get("bars"))
        # Self-healing: polling must restart a stalled build rather than
        # report a cold cache forever.
        if not refreshing and (not cached or not history_ready):
            self._start_oi_finder_chart_refresh(target, full_history=bool(cached))
            refreshing = True
        return {
            "symbol": target,
            "historyReady": history_ready,
            "refreshing": refreshing,
            "hasBars": has_bars,
            "warming": not has_bars and refreshing,
        }

    @classmethod
    def _fine_chart_study_tail(cls, rows: object) -> list:
        """Return only the intraday tail from the daily+30m study archive."""
        if not isinstance(rows, list) or len(rows) < 3:
            return list(rows or []) if isinstance(rows, list) else []
        day_seconds = 86_400
        fine_start = 0
        # Match the browser's cadence boundary: the daily archive has repeated
        # day-size gaps, while a weekend gap inside the 30m tail is isolated.
        for index in range(len(rows) - 3, -1, -1):
            try:
                gap = int(rows[index + 1].get("time") or 0) - int(rows[index].get("time") or 0)
                next_gap = int(rows[index + 2].get("time") or 0) - int(rows[index + 1].get("time") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if gap >= day_seconds and next_gap >= day_seconds:
                fine_start = index + 2
                break
        return rows[fine_start:][-cls.OI_CHART_INITIAL_STUDY_BAR_COUNT:]

    def _slim_initial_chart_payload(
        self,
        payload: dict,
        include_study_seed: bool = False,
    ) -> dict:
        bars = payload.get("bars")
        if not isinstance(bars, list) or not bars:
            return payload
        slim = {
            key: value
            for key, value in payload.items()
            if key not in self.OI_CHART_INITIAL_SLIM_KEYS
        }
        slim["bars"] = bars[-self.OI_CHART_INITIAL_BAR_COUNT:]
        daily_bars = slim.get("dailyBars")
        if isinstance(daily_bars, list):
            slim["dailyBars"] = daily_bars[-self.OI_CHART_INITIAL_DAILY_BAR_COUNT:]
        if include_study_seed:
            slim["studyBars"] = self._fine_chart_study_tail(payload.get("studyBars"))
            slim["initialStudySeed"] = True
        slim["historyLoading"] = True
        slim["refreshing"] = True
        slim["initialSlim"] = True
        return slim

    def _delta_chart_payload(self, payload: dict, since_epoch: float) -> dict:
        """Tail-only reconcile; mirrors frontend mergeChartDeltaPayload."""
        cutoff = max(0.0, float(since_epoch) - self.OI_CHART_DELTA_OVERLAP_SECONDS)

        def tail(rows: object) -> list:
            if not isinstance(rows, list):
                return []
            return [row for row in rows if float((row or {}).get("time") or 0) >= cutoff]

        delta = {
            key: value
            for key, value in payload.items()
            if key not in {"bars", "studyBars", "dailyBars"}
        }
        delta["delta"] = True
        delta["deltaSince"] = cutoff
        delta["bars"] = tail(payload.get("bars"))
        delta["studyBars"] = tail(payload.get("studyBars"))
        delta["dailyBars"] = (payload.get("dailyBars") or [])[-3:]
        return delta

    def oi_finder_chart_payload(
        self,
        symbol: str,
        initial_paint: bool = False,
        since_epoch: float = 0.0,
        prefetch: bool = False,
        refresh: bool = False,
        include_study_seed: bool = False,
    ) -> dict:
        """Return chart cache immediately while Schwab refreshes off-thread."""
        target = (symbol or "").strip().upper()
        self.touch_oi_finder_interactive_window()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", target):
            return self._build_oi_finder_chart_payload(target, fast_start=True)

        with self.oi_finder_chart_lock:
            cached = self.oi_finder_chart_cache.get(target)
            refreshing = target in self.oi_finder_chart_refreshes
        if not cached:
            disk_payload = self._load_oi_finder_chart_disk_payload(target)
            if disk_payload is not None:
                # Staleness is a property of the DATA, not the file. The disk
                # TTL is five days, so a tape whose newest bar is from a prior
                # session still loaded as "ready" — which meant the follow-up
                # refresh was recent-only and spliced today's minutes onto a
                # days-old deep tape, leaving a visible gap and stale 4H/D/W/M
                # panes. A tape that does not reach the current session is not
                # ready, so it earns a full rebuild instead.
                disk_bars = disk_payload.get("bars") or []
                newest_bar_time = int(disk_bars[-1].get("time") or 0) if disk_bars else 0
                tape_age_seconds = max(0.0, time.time() - newest_bar_time) if newest_bar_time else float("inf")
                tape_current = tape_age_seconds <= OI_FINDER_CHART_STALE_TAPE_SECONDS
                history_ready = (
                    not bool(disk_payload.get("historyLoading"))
                    and self._chart_payload_has_twenty_year_four_hour_archive(disk_payload)
                    and self._chart_payload_has_multi_timeframe_depth(disk_payload)
                    and self._chart_payload_has_ready_ganesh_signals(disk_payload)
                    and tape_current
                )
                disk_payload["studySchemaStale"] = not self._chart_payload_has_ready_ganesh_signals(
                    disk_payload,
                )
                cached = {
                    "cached_at": time.monotonic(),
                    "payload": disk_payload,
                    "history_ready": history_ready,
                }
                with self.oi_finder_chart_lock:
                    self.oi_finder_chart_cache[target] = cached
                if not prefetch:
                    self._start_oi_finder_chart_refresh(target, full_history=not history_ready)
                    refreshing = True
        if cached:
            age_seconds = max(0.0, time.monotonic() - float(cached.get("cached_at", 0)))
            history_ready = bool(cached.get("history_ready")) and self._chart_payload_has_ready_ganesh_signals(
                cached.get("payload"),
            ) and self._chart_payload_has_multi_timeframe_depth(cached.get("payload"))
            if not history_ready:
                cached["history_ready"] = False
            should_refresh = (
                refresh
                or (not history_ready and not prefetch)
                or (age_seconds >= OI_FINDER_CHART_REFRESH_SECONDS and not prefetch)
            )
            if should_refresh and not refreshing:
                # A ready cache already owns the 20-year 4H archive and its
                # expensive indicator replay. Refresh only the recent REST
                # tail so normal chart polling cannot continuously queue a
                # full-history rebuild every five seconds.
                self._start_oi_finder_chart_refresh(target, full_history=not history_ready)
                refreshing = True
            payload = dict(cached["payload"])
            payload["cacheAgeSeconds"] = round(age_seconds, 2)
            payload["historyLoading"] = not history_ready
            payload["studySchemaStale"] = not self._chart_payload_has_ready_ganesh_signals(payload)
            payload["refreshing"] = refreshing
            # Window at SERVE time, not just at build time: payloads persisted
            # to disk before the windowing fix still carry the whole
            # multi-year replay (6,000+ signals), and they are served verbatim
            # until something rebuilds them. Trimming here bounds every path.
            payload = self._windowed_ganesh_chart_payload(payload)
            if since_epoch > 0 and history_ready:
                return self._delta_chart_payload(payload, since_epoch)
            if initial_paint:
                return self._slim_initial_chart_payload(
                    payload,
                    include_study_seed=include_study_seed,
                )
            return payload

        # Only the small recent price-history request blocks a cold ticker.
        payload = self._build_oi_finder_chart_payload(target, fast_start=True)
        if payload.get("bars"):
            history_ready = not bool(payload.get("historyLoading"))
            with self.oi_finder_chart_lock:
                self.oi_finder_chart_cache[target] = {
                    "cached_at": time.monotonic(),
                    "payload": payload,
                    "history_ready": history_ready,
                }
            # Let the small seed response paint before doing any deep replay.
            # A selected chart's readiness poll promotes the cached seed to
            # full history; speculative prefetches do no background work.
            if not prefetch:
                self._start_oi_finder_chart_refresh(target, full_history=False)
        if initial_paint:
            return self._slim_initial_chart_payload(
                payload,
                include_study_seed=include_study_seed,
            )
        return payload

    def why_not_traded(self, symbol: str) -> dict:
        target = str(symbol or "").strip().upper()
        diagnostics = self.scanner.diagnose_symbol(target)
        trade_history = self._enrich_trade_history(self.repository.get_trade_history(limit=200))
        symbol_trades = pd.DataFrame()
        if trade_history is not None and not trade_history.empty and "symbol" in trade_history.columns:
            symbol_trades = trade_history[trade_history["symbol"].astype(str).str.upper() == target]

        payload = {
            "symbol": target,
            "diagnostics": diagnostics,
            "trade": _serialize_value(symbol_trades.iloc[0].to_dict()) if not symbol_trades.empty else None,
        }

        scan_result = diagnostics.get("scanResult")
        if scan_result:
            candidate_frame = self.strategy_frame_for_symbols([target])
            if not candidate_frame.empty:
                payload["candidate"] = _serialize_value(candidate_frame.iloc[0].to_dict())
            else:
                payload["candidate"] = None
        else:
            payload["candidate"] = None
        return payload

    def scan_diagnostics(self, symbols: list[str]) -> dict:
        requested = [str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()]
        if not requested:
            requested = ["BBWI", "CART", "CBOE", "DG", "HLT", "LOW"]

        def rule_snapshot(symbol: str, signal_bars: pd.DataFrame) -> dict:
            one_hour = self.scanner._aggregate_signal_bars(signal_bars, 60)
            four_hour = self.scanner._aggregate_signal_bars(signal_bars, 240)
            if signal_bars.empty or len(one_hour) < 3 or len(four_hour) < 3:
                return {
                    "oneMinuteBars": len(signal_bars),
                    "oneHourBars": len(one_hour),
                    "fourHourBars": len(four_hour),
                    "error": "not enough live chart bars",
                }

            last_price = float(signal_bars.iloc[-1]["close"] or 0.0)
            one_hour_scan = scan_live_price_change(
                symbol,
                one_hour,
                threshold_pct=settings.scanner.min_one_hour_close_change_pct,
            )
            four_hour_volume_scan = scan_live_4h_volume(
                symbol,
                four_hour,
                threshold_pct=settings.scanner.min_four_hour_volume_change_pct,
            )
            return {
                "lastPrice": round(last_price, 4),
                "pricePass": last_price >= settings.scanner.min_price,
                "oneMinuteBars": len(signal_bars),
                "latestOneMinute": _serialize_value(signal_bars.iloc[-1].to_dict()),
                "oneHourBars": len(one_hour),
                "latestOneHour": _serialize_value(one_hour.iloc[-1].to_dict()),
                "oneHourTwoBarsAgo": _serialize_value(one_hour.iloc[-3].to_dict()),
                "oneHourCloseChangePct": one_hour_scan["price_change_pct"],
                "oneHourClosePass": one_hour_scan["signal"],
                "fourHourBars": len(four_hour),
                "latestFourHour": _serialize_value(four_hour.iloc[-1].to_dict()),
                "fourHourTwoBarsAgo": _serialize_value(four_hour.iloc[-3].to_dict()),
                "fourHourVolumeChangePct": four_hour_volume_scan["volume_change_pct"],
                "fourHourVolumePass": four_hour_volume_scan["signal"],
                "allPass": bool(
                    last_price >= settings.scanner.min_price
                    and one_hour_scan["signal"]
                    and four_hour_volume_scan["signal"]
                ),
            }

        def aggregate_with_offset(signal_bars: pd.DataFrame, bucket_minutes: int, offset_minutes: int) -> pd.DataFrame:
            if signal_bars is None or signal_bars.empty or "timestamp" not in signal_bars.columns:
                return pd.DataFrame()
            frame = signal_bars.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
            if frame.empty:
                return pd.DataFrame()
            frame["timestamp"] = frame["timestamp"] - pd.Timedelta(minutes=int(offset_minutes))
            aggregated = (
                frame.set_index("timestamp")
                .resample(f"{int(bucket_minutes)}min", label="right", closed="right")
                .agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volume": "sum",
                    }
                )
                .dropna(subset=["open", "high", "low", "close"])
                .reset_index()
            )
            aggregated["timestamp"] = aggregated["timestamp"] + pd.Timedelta(minutes=int(offset_minutes))
            return aggregated

        def offset_snapshot(symbol: str, signal_bars: pd.DataFrame, offset_minutes: int) -> dict:
            one_hour = aggregate_with_offset(signal_bars, 60, offset_minutes)
            four_hour = aggregate_with_offset(signal_bars, 240, offset_minutes)
            if signal_bars.empty or len(one_hour) < 3 or len(four_hour) < 3:
                return {"allPass": False, "error": "not enough live chart bars"}
            last_price = float(signal_bars.iloc[-1]["close"] or 0.0)
            one_hour_scan = scan_live_price_change(
                symbol,
                one_hour,
                threshold_pct=settings.scanner.min_one_hour_close_change_pct,
            )
            four_hour_volume_scan = scan_live_4h_volume(
                symbol,
                four_hour,
                threshold_pct=settings.scanner.min_four_hour_volume_change_pct,
            )
            return {
                "allPass": bool(
                    last_price >= settings.scanner.min_price
                    and one_hour_scan["signal"]
                    and four_hour_volume_scan["signal"]
                ),
                "oneHourCloseChangePct": one_hour_scan["price_change_pct"],
                "fourHourVolumeChangePct": four_hour_volume_scan["volume_change_pct"],
                "latestOneHourEnd": _serialize_value(one_hour.iloc[-1]["timestamp"]),
                "latestFourHourEnd": _serialize_value(four_hour.iloc[-1]["timestamp"]),
            }

        rows = []
        for symbol in requested:
            signal_bars = self.scanner._tos_signal_source_bars(symbol)
            no_zero_bars = signal_bars[signal_bars["volume"].fillna(0) > 0].copy() if not signal_bars.empty else signal_bars
            if not signal_bars.empty:
                minutes = (signal_bars["timestamp"].dt.hour * 60) + signal_bars["timestamp"].dt.minute
                regular_bars = signal_bars[
                    (signal_bars["timestamp"].dt.weekday < 5)
                    & (minutes >= (9 * 60 + 30))
                    & (minutes <= (16 * 60))
                ].copy()
            else:
                regular_bars = signal_bars
            rows.append(
                {
                    "symbol": symbol,
                    "extended": rule_snapshot(symbol, signal_bars),
                    "noZeroVolume": rule_snapshot(symbol, no_zero_bars),
                    "regularSession": rule_snapshot(symbol, regular_bars),
                    "offsets": {
                        str(offset): offset_snapshot(symbol, signal_bars, offset)
                        for offset in [0, 30, 240, 570, 780, 930, 960]
                    },
                }
            )
        return {
            "rules": {
                "minPrice": settings.scanner.min_price,
                "oneHourClosePct": settings.scanner.min_one_hour_close_change_pct,
                "fourHourVolumePct": settings.scanner.min_four_hour_volume_change_pct,
                "length": settings.scanner.signal_lookback_bars,
                "includeExtendedHours": settings.scanner.signal_include_extended_hours,
            },
            "rows": rows,
        }

    def strategy_frame_for_symbols(self, symbols: list[str], max_results: int | None = None) -> pd.DataFrame:
        scan_result = self.scanner.run(
            symbols,
            max_results=max_results,
            ignore_one_hour_price_change=True,
            ignore_four_hour_price_change=True,
            ignore_four_hour_volume=True,
            ignore_ema9_retest=True,
        )
        if scan_result.empty:
            return pd.DataFrame()
        scan_result = self.trader.ai_model.score_frame(scan_result)
        candidates = self.trader.strategy.build_trade_candidates(
            scan_result,
            existing_symbols=self.trader._existing_symbols(),
            symbol_memory=self.repository.get_symbol_memory(limit=500),
            catalysts=self._recent_catalysts_or_empty(limit=500),
        )
        candidate_frame = self.trader.strategy.candidates_to_frame(candidates)
        if candidate_frame.empty:
            return candidate_frame
        extra_columns = [column for column in scan_result.columns if column not in candidate_frame.columns and column != "symbol"]
        if extra_columns:
            candidate_frame = candidate_frame.merge(
                scan_result[["symbol", *extra_columns]],
                on="symbol",
                how="left",
            )
        return candidate_frame

    def option_strategy_frame_for_symbols(self, symbols: list[str], max_results: int | None = None) -> pd.DataFrame:
        scan_result = self.scanner.run(
            symbols,
            max_results=max_results,
            ignore_one_hour_price_change=True,
            ignore_four_hour_price_change=True,
            ignore_four_hour_volume=True,
            ignore_ema9_retest=True,
        )
        if scan_result.empty:
            return pd.DataFrame()
        scan_result = self.trader.ai_model.score_frame(scan_result)
        candidates = self.trader.strategy.build_trade_candidates(
            scan_result,
            existing_symbols=self.trader._existing_symbols(),
            symbol_memory=self.repository.get_symbol_memory(limit=500),
            catalysts=self._recent_catalysts_or_empty(limit=500),
        )
        candidate_frame = self.trader.strategy.candidates_to_frame(candidates)
        if candidate_frame.empty:
            return candidate_frame
        extra_columns = [column for column in scan_result.columns if column not in candidate_frame.columns and column != "symbol"]
        if extra_columns:
            candidate_frame = candidate_frame.merge(
                scan_result[["symbol", *extra_columns]],
                on="symbol",
                how="left",
            )
        return candidate_frame

    def _option_automation_agents(self, option_positions: pd.DataFrame | None = None) -> list[dict]:
        open_count = 0 if option_positions is None or option_positions.empty else len(option_positions)
        market_source = "Schwab/TOS chain" if settings.market_data_provider == "schwab" else "waiting for Schwab/TOS chain"
        supervisor_report = getattr(self, "option_supervisor_report", {}) or {}
        supervisor_llm = supervisor_report.get("llm") or {}
        return [
            {
                "name": "Signal Agent",
                "status": "Running" if self.option_bot_state == "Running" else self.option_bot_state,
                "detail": "Checks the live 5m EMA 9/21/50 trend, VWAP, momentum trigger, and setup quality. EMA9 retest, 1H/4H changes, and volume acceleration are informational only.",
            },
            {
                "name": "Contract Agent",
                "status": market_source,
                "detail": "Chooses long calls by delta cap, mid price, valid bid/ask, spread, and expected move.",
            },
            {
                "name": "Risk Agent",
                "status": f"{open_count} open option position(s)",
                "detail": "Sets premium stop, target 1, target contracts, and max debit.",
            },
            {
                "name": "Runner Agent",
                "status": "Active" if open_count else "Idle",
                "detail": "Moves remaining contracts to break even after target 1 and trails premium gains.",
            },
            {
                "name": "LLM Supervisor Agent",
                "status": supervisor_report.get("status") or "Waiting For Scan",
                "detail": (
                    "Advisory-only review. Explains taken/skipped trades, flags anomalies, summarizes catalysts, "
                    f"and suggests tuning. Mode={supervisor_llm.get('mode', 'not_started')}."
                ),
            },
        ]

    def _option_positions_frame(self, positions: list, option_trade_history: pd.DataFrame) -> pd.DataFrame:
        if not positions:
            return pd.DataFrame()

        trade_map: dict[str, dict] = {}
        if option_trade_history is not None and not option_trade_history.empty:
            ordered = option_trade_history.copy()
            ordered["opened_at"] = pd.to_datetime(ordered.get("opened_at"), errors="coerce", utc=True)
            ordered = ordered.sort_values("opened_at", ascending=False)
            for row in ordered.to_dict("records"):
                option_symbol = self.option_client.normalize_option_symbol(
                    row.get("option_symbol") or row.get("selected_option_symbol") or row.get("contract") or ""
                )
                if option_symbol and option_symbol not in trade_map:
                    trade_map[option_symbol] = row

        rows: list[dict] = []
        for position in positions:
            option_symbol = self.option_client.normalize_option_symbol(str(getattr(position, "symbol", "")))
            if not option_symbol:
                continue
            trade_meta = trade_map.get(option_symbol, {})
            plan = self._option_trade_plan_state(trade_meta) if trade_meta else {}
            quantity = abs(self._safe_float(getattr(position, "qty", None), trade_meta.get("quantity") or 0.0))
            entry_price = self._safe_float(getattr(position, "avg_entry_price", None), trade_meta.get("entry_price") or plan.get("entry_mid") or 0.0)
            current_mid = self._safe_float(getattr(position, "current_price", None), trade_meta.get("current_mid") or plan.get("current_mid") or entry_price)
            realized_pnl = self._safe_float(plan.get("realized_pnl"), trade_meta.get("realized_pnl") or 0.0)
            unrealized_pnl = self._safe_float(getattr(position, "unrealized_pl", None), (current_mid - entry_price) * quantity * OPTION_CONTRACT_MULTIPLIER)
            rows.append(
                {
                    **trade_meta,
                    "symbol": trade_meta.get("underlying_symbol") or trade_meta.get("symbol"),
                    "underlying_symbol": trade_meta.get("underlying_symbol") or trade_meta.get("symbol"),
                    "contract": option_symbol,
                    "option_symbol": option_symbol,
                    "quantity": quantity,
                    "remaining_quantity": quantity,
                    "entry_price": entry_price,
                    "current_mid": current_mid,
                    "selected_option_mid": current_mid,
                    "selected_option_symbol": option_symbol,
                    "selected_option_expiry": trade_meta.get("selected_option_expiry") or plan.get("selected_option_expiry"),
                    "selected_option_strike": trade_meta.get("selected_option_strike") or plan.get("selected_option_strike"),
                    "selected_option_delta": trade_meta.get("selected_option_delta") or plan.get("selected_option_delta"),
                    "selected_option_expected_move": trade_meta.get("selected_option_expected_move") or plan.get("selected_option_expected_move"),
                    "selected_option_volume": trade_meta.get("selected_option_volume") or plan.get("selected_option_volume"),
                    "selected_option_open_interest": trade_meta.get("selected_option_open_interest") or plan.get("selected_option_open_interest"),
                    "underlying_target_1_strike": trade_meta.get("underlying_target_1_strike") or plan.get("underlying_target_1_strike"),
                    "underlying_target_1_sell_percent": trade_meta.get("underlying_target_1_sell_percent") or plan.get("underlying_target_1_sell_percent"),
                    "liquidity_breakout_required": trade_meta.get("liquidity_breakout_required") or plan.get("liquidity_breakout_required"),
                    "liquidity_atm_dominates_otm": trade_meta.get("liquidity_atm_dominates_otm") or plan.get("liquidity_atm_dominates_otm"),
                    "underlying_target_liquidity_metric": trade_meta.get("underlying_target_liquidity_metric") or plan.get("underlying_target_liquidity_metric"),
                    "runner_stop": plan.get("runner_stop") or trade_meta.get("runner_stop") or trade_meta.get("stop_price"),
                    "take_profit_1": plan.get("take_profit_1") or trade_meta.get("take_profit_1") or trade_meta.get("target_price"),
                    "realized_pnl": round(realized_pnl, 2),
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "marked_pnl": round(realized_pnl + unrealized_pnl, 2),
                    "pnl": round(realized_pnl + unrealized_pnl, 2),
                    "status": "position_open",
                }
            )
        return pd.DataFrame(rows)

    def set_scheduler(self, enabled: bool, interval_seconds: int | None = None) -> dict:
        if interval_seconds:
            self.scheduler_interval_seconds = max(5, int(interval_seconds))
        self.scheduler_enabled = enabled
        self.scheduler_cycle_status = "Starting" if enabled else "Paused"
        self.scheduler_cycle_message = "Automation scheduler is running." if enabled else "Automation scheduler is paused."
        if enabled and (self.scheduler_thread is None or not self.scheduler_thread.is_alive()):
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
        self.action_message = f"Automation scheduler {'started' if enabled else 'paused'}."
        self.repository.log_bot_event("scheduler", self.action_message)
        return self.dashboard_payload()

    def _start_option_scheduler(self) -> None:
        if self.option_scheduler_thread is None or not self.option_scheduler_thread.is_alive():
            self.option_scheduler_thread = threading.Thread(target=self._option_scheduler_loop, daemon=True)
            self.option_scheduler_thread.start()

    def _start_stock_position_manager(self) -> None:
        if self.stock_position_manager_thread is None or not self.stock_position_manager_thread.is_alive():
            self.stock_position_manager_thread = threading.Thread(
                target=self._stock_position_manager_loop,
                name="stock-position-manager-engine",
                daemon=True,
            )
            self.stock_position_manager_thread.start()

    def _stock_position_manager_loop(self) -> None:
        while True:
            self.stock_position_manager_last_run = datetime.now().astimezone()
            self.stock_position_manager_status = "Managing"
            try:
                self.trader.sync_order_statuses()
                self.trader.manage_open_trades()
                self.stock_position_manager_last_error = ""
                self.stock_position_manager_status = "Monitoring"
            except Exception as exc:
                self.stock_position_manager_last_error = str(exc)
                self.stock_position_manager_status = "Error"
                self.repository.log_bot_event("stock_position_manager_error", str(exc))
            time.sleep(max(int(self.stock_position_manager_interval_seconds), 1))

    def _refresh_learning_status_cache(self) -> dict:
        payload = self.repository.learning_status()
        try:
            payload["catalystShadowStudy"] = self.repository.catalyst_shadow_study_status()
        except Exception as exc:
            payload["catalystShadowStudy"] = {
                "status": "UNAVAILABLE",
                "verdict": "COLLECTING",
                "message": f"Catalyst shadow diagnostics are temporarily unavailable: {exc}",
                "shadowOnly": True,
                "canBlockTrades": False,
                "canRankTrades": False,
                "canSizeTrades": False,
                "canDelayExecution": False,
            }
        lock = getattr(self, "learning_status_cache_lock", None)
        if lock is None:
            self.learning_status_cache_lock = threading.Lock()
            lock = self.learning_status_cache_lock
        with lock:
            self.learning_status_cache = payload
            self.learning_status_cache_timestamp = datetime.now().astimezone()
        return payload

    def _learning_status_payload(self) -> dict:
        lock = getattr(self, "learning_status_cache_lock", None)
        cached = getattr(self, "learning_status_cache", None)
        if cached is None:
            try:
                cached = self._refresh_learning_status_cache()
            except Exception:
                cached = {}
        elif lock is not None:
            with lock:
                cached = dict(getattr(self, "learning_status_cache", None) or {})
        payload = dict(cached or {})
        scope_symbols = self._learning_symbol_cohorts()
        payload["scope"] = {
            "id": "cohort-comparison",
            "label": "Mag7 vs Watchlist 400",
            "symbols": sorted(scope_symbols),
            "symbolCount": len(scope_symbols),
            "mag7Count": sum(1 for cohort in scope_symbols.values() if cohort == "mag7"),
            "watchlistCount": sum(1 for cohort in scope_symbols.values() if cohort == "watchlist"),
        }
        payload["agent"] = {
            "status": self.learning_agent.status,
            "message": self.learning_agent.message,
            "intervalSeconds": self.learning_interval_seconds,
            "lastRun": _serialize_value(self.learning_agent.last_run),
            "lastError": self.learning_agent.last_error,
            "lastResult": _serialize_value(self.learning_last_result),
        }
        payload["cacheAsOf"] = _serialize_value(getattr(self, "learning_status_cache_timestamp", None))
        return payload

    def _start_learning_loop(self) -> None:
        if self.learning_thread is None or not self.learning_thread.is_alive():
            self.learning_thread = threading.Thread(
                target=self._learning_loop,
                name="trading-learning-agent",
                daemon=True,
            )
            self.learning_thread.start()

    def _start_runtime_watchdog(self) -> None:
        if not self.runtime_watchdog_enabled:
            self.runtime_watchdog_status = "Disabled"
            return
        if self.runtime_watchdog_thread is None or not self.runtime_watchdog_thread.is_alive():
            self.runtime_watchdog_thread = threading.Thread(
                target=self._runtime_watchdog_loop,
                name="agentic-runtime-watchdog",
                daemon=True,
            )
            self.runtime_watchdog_thread.start()

    def _runtime_component_state(
        self,
        name: str,
        thread: threading.Thread | None,
        required: bool,
        last_run: datetime | None = None,
        expected_interval_seconds: float | int | None = None,
    ) -> dict:
        alive = bool(thread is not None and thread.is_alive())
        age_seconds = None
        stale = False
        if last_run is not None:
            timestamp = last_run
            if timestamp.tzinfo is None:
                timestamp = timestamp.astimezone()
            age_seconds = max((datetime.now().astimezone() - timestamp).total_seconds(), 0.0)
            if expected_interval_seconds:
                stale_limit = max(
                    float(expected_interval_seconds) * self.runtime_watchdog_stale_multiplier,
                    60.0,
                )
                stale = age_seconds > stale_limit
        healthy = (not required) or (alive and not stale)
        return {
            "name": name,
            "required": required,
            "alive": alive,
            "healthy": healthy,
            "stale": stale,
            "lastRun": _serialize_value(last_run),
            "ageSeconds": round(age_seconds, 2) if age_seconds is not None else None,
        }

    def _runtime_watchdog_snapshot(self) -> dict[str, dict]:
        components = {
            "stockScanner": self._runtime_component_state(
                "Stock scanner",
                self.scanner_auto_thread,
                self.scanner_auto_enabled,
                self.scanner_auto_last_run,
                self.scanner_auto_interval_seconds,
            ),
            "mag7OiScanner": self._runtime_component_state(
                "MAG7 OI scanner",
                self.oi_mag7_auto_thread,
                self.oi_scanner_auto_enabled,
                self.oi_mag7_auto_last_run,
                max(self.oi_mag7_auto_interval_seconds, 30),
            ),
            "watchlistOiScanner": self._runtime_component_state(
                "Watchlist OI scanner",
                self.oi_watchlist_auto_thread,
                # The full-watchlist worker is intentionally disabled below in
                # _start_oi_scanner_auto_loops; only the saved MAG7 worker runs.
                False,
                self.oi_watchlist_auto_last_run,
                max(self.oi_watchlist_auto_interval_seconds, 30),
            ),
            "stockScheduler": self._runtime_component_state(
                "Stock scheduler",
                self.scheduler_thread,
                self.scheduler_enabled,
                self.scheduler_last_run,
                # A complete watchlist scan plus order synchronization can run
                # for several minutes. Keep thread liveness immediate, while
                # allowing an in-flight cycle to finish before declaring it stale.
                max(self.scheduler_interval_seconds, 180),
            ),
            "optionScheduler": self._runtime_component_state(
                "Option scheduler",
                self.option_scheduler_thread,
                self.option_bot_state == "Running",
                self.option_scheduler_last_run,
                # A full option pass can spend over a minute in live chain retrieval.
                # Thread liveness still fails immediately; this window avoids treating
                # an in-flight scan as stale before it can publish its next heartbeat.
                max(self.option_scheduler_interval_seconds, 60),
            ),
            "stockPositionManager": self._runtime_component_state(
                "Stock position manager",
                self.stock_position_manager_thread,
                True,
                self.stock_position_manager_last_run,
                self.stock_position_manager_interval_seconds,
            ),
            "learningAgent": self._runtime_component_state(
                "Learning agent",
                self.learning_thread,
                True,
                self.learning_agent.last_run,
                self.learning_interval_seconds,
            ),
        }
        try:
            self.repository.get_app_settings()
            components["database"] = {
                "name": "SQLite database",
                "required": True,
                "alive": True,
                "healthy": True,
                "stale": False,
                "lastRun": _serialize_value(datetime.now().astimezone()),
                "ageSeconds": 0.0,
            }
        except Exception as exc:
            components["database"] = {
                "name": "SQLite database",
                "required": True,
                "alive": False,
                "healthy": False,
                "stale": False,
                "lastRun": None,
                "ageSeconds": None,
                "error": str(exc),
            }
        return components

    def _recover_runtime_components(self, components: dict[str, dict]) -> list[str]:
        if not self.runtime_watchdog_auto_recover:
            return []
        recovered: list[str] = []
        recovery_actions = {
            "stockScanner": self._start_scanner_auto_loop,
            "mag7OiScanner": self._start_oi_scanner_auto_loops,
            "stockPositionManager": self._start_stock_position_manager,
            "learningAgent": self._start_learning_loop,
            "optionScheduler": self._start_option_scheduler,
        }
        for key, action in recovery_actions.items():
            component = components.get(key) or {}
            if component.get("required") and not component.get("alive"):
                action()
                recovered.append(key)
        stock_scheduler = components.get("stockScheduler") or {}
        if stock_scheduler.get("required") and not stock_scheduler.get("alive"):
            self.scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="stock-automation-scheduler",
                daemon=True,
            )
            self.scheduler_thread.start()
            recovered.append("stockScheduler")
        return sorted(set(recovered))

    def _runtime_watchdog_loop(self) -> None:
        time.sleep(8)
        while self.runtime_watchdog_enabled:
            self.runtime_watchdog_last_run = datetime.now().astimezone()
            try:
                components = self._runtime_watchdog_snapshot()
                recovered = self._recover_runtime_components(components)
                if recovered:
                    self.runtime_watchdog_recoveries += len(recovered)
                    self.repository.log_bot_event(
                        "runtime_watchdog_recovery",
                        f"Runtime watchdog restarted: {', '.join(recovered)}.",
                        json.dumps({"components": recovered}, default=str),
                    )
                    components = self._runtime_watchdog_snapshot()
                unhealthy = sorted(
                    key for key, component in components.items()
                    if component.get("required") and not component.get("healthy")
                )
                signature = "|".join(unhealthy)
                if signature and signature != self.runtime_watchdog_last_incident_signature:
                    self.runtime_watchdog_incidents += 1
                    self.repository.log_bot_event(
                        "runtime_watchdog_incident",
                        f"Runtime watchdog detected unhealthy components: {', '.join(unhealthy)}.",
                        json.dumps({"components": components}, default=str),
                    )
                self.runtime_watchdog_last_incident_signature = signature
                self.runtime_watchdog_components = components
                self.runtime_watchdog_status = "Healthy" if not unhealthy else "Degraded"
                self.runtime_watchdog_last_error = "" if not unhealthy else f"Unhealthy: {', '.join(unhealthy)}"
            except Exception as exc:
                self.runtime_watchdog_status = "Error"
                self.runtime_watchdog_last_error = str(exc)
                self.repository.log_bot_event("runtime_watchdog_error", str(exc))
            # Runtime health is merged into cached dashboard responses, so the
            # watchdog must not invalidate and rebuild the heavyweight broker /
            # trade-history payload on every heartbeat.
            deadline = time.monotonic() + self.runtime_watchdog_interval_seconds
            while self.runtime_watchdog_enabled and time.monotonic() < deadline:
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))

    def _runtime_health_payload(self) -> dict:
        return {
            "enabled": self.runtime_watchdog_enabled,
            "autoRecover": self.runtime_watchdog_auto_recover,
            "status": self.runtime_watchdog_status,
            "intervalSeconds": self.runtime_watchdog_interval_seconds,
            "lastRun": _serialize_value(self.runtime_watchdog_last_run),
            "lastError": self.runtime_watchdog_last_error,
            "incidents": self.runtime_watchdog_incidents,
            "recoveries": self.runtime_watchdog_recoveries,
            "components": _serialize_value(self.runtime_watchdog_components),
        }

    def _learning_loop(self) -> None:
        time.sleep(15)
        while True:
            self.learning_last_result = self.learning_agent.run_cycle()
            try:
                self._refresh_learning_status_cache()
            except Exception as exc:
                self.repository.log_bot_event("learning_status_cache_error", str(exc))
            self._invalidate_dashboard_cache()
            deadline = time.monotonic() + self.learning_interval_seconds
            while time.monotonic() < deadline:
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))

    def start_learning_cycle(self, force_training: bool = False) -> dict:
        if self.learning_cycle_thread is not None and self.learning_cycle_thread.is_alive():
            return {
                "started": False,
                "busy": True,
                "dashboard": {"learning": self._learning_status_payload()},
            }

        self.learning_agent.status = "Queued"
        self.learning_agent.message = "Manual learning cycle queued."

        def _runner() -> None:
            self.learning_last_result = self.learning_agent.run_cycle(force_training=force_training)
            try:
                self._refresh_learning_status_cache()
            except Exception as exc:
                self.repository.log_bot_event("learning_status_cache_error", str(exc))
            self._invalidate_dashboard_cache()

        self.learning_cycle_thread = threading.Thread(
            target=_runner,
            name="manual-learning-cycle",
            daemon=True,
        )
        self.learning_cycle_thread.start()
        return {
            "started": True,
            "busy": False,
            "dashboard": {"learning": self._learning_status_payload()},
        }
    def _start_scanner_auto_loop(self) -> None:
        if not self.scanner_auto_enabled:
            return
        if self.scanner_auto_thread is None or not self.scanner_auto_thread.is_alive():
            self.scanner_auto_thread = threading.Thread(
                target=self._scanner_auto_loop,
                name="stock-scanner-engine",
                daemon=True,
            )
            self.scanner_auto_thread.start()

    def _start_oi_scanner_auto_loops(self, initial_delay_seconds: float = 0.0) -> None:
        if not self.oi_scanner_auto_enabled:
            return
        if self.oi_mag7_auto_thread is None or not self.oi_mag7_auto_thread.is_alive():
            delay = max(0.0, float(initial_delay_seconds or 0.0))

            def run_mag7_loop() -> None:
                # The CPU-heavy scanner must never run before the HTTP server
                # binds: it can starve startup so badly that port 3001 never
                # comes up and the watchdog kill/respawns in a loop.
                if delay:
                    time.sleep(delay)
                self._oi_scanner_auto_loop("mag7")

            self.oi_mag7_auto_thread = threading.Thread(
                target=run_mag7_loop,
                name="mag7-oi-scanner-engine",
                daemon=True,
            )
            self.oi_mag7_auto_thread.start()
        # The full-watchlist OI worker is intentionally never started.
        self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
        self.oi_watchlist_auto_next_run = None

    def _next_oi_finder_snapshot_time(self, now: datetime | None = None) -> datetime:
        """Return the next weekday after-close archival window in Eastern Time."""
        eastern = ZoneInfo(EASTERN_TZ)
        current = (now or datetime.now(eastern)).astimezone(eastern)
        candidate = current.replace(
            hour=self.oi_finder_snapshot_hour_et,
            minute=self.oi_finder_snapshot_minute_et,
            second=0,
            microsecond=0,
        )
        if current >= candidate:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _oi_finder_snapshot_schedule_payload(self) -> dict:
        return {
            "enabled": self.oi_finder_snapshot_enabled,
            "status": self.oi_finder_snapshot_status,
            "message": self.oi_finder_snapshot_message,
            "schedule": f"Weekdays {self.oi_finder_snapshot_hour_et:02d}:{self.oi_finder_snapshot_minute_et:02d} ET",
            "intervalSeconds": self.oi_finder_snapshot_interval_seconds,
            "lastRun": _serialize_value(self.oi_finder_snapshot_last_run),
            "nextRun": _serialize_value(self.oi_finder_snapshot_next_run),
            "lastError": self.oi_finder_snapshot_last_error,
            "progress": dict(self.oi_finder_snapshot_progress),
            "mag7Live": {
                "enabled": self.oi_finder_mag7_live_enabled,
                "status": self.oi_finder_mag7_live_status,
                "message": self.oi_finder_mag7_live_message,
                "intervalSeconds": self.oi_finder_mag7_live_interval_seconds,
                "lastRun": _serialize_value(self.oi_finder_mag7_live_last_run),
                "nextRun": _serialize_value(self.oi_finder_mag7_live_next_run),
                "lastError": self.oi_finder_mag7_live_last_error,
                "progress": dict(self.oi_finder_mag7_live_progress),
                "scope": "Saved MAG7 scanner watchlist only",
            },
            "scope": "Saved watchlist — 0-31 DTE option contracts",
            "safety": "Sequential after-close snapshots only; no continuous full-watchlist option-chain scanning.",
        }

    def _next_oi_finder_mag7_live_session(self, now: datetime | None = None) -> datetime:
        """Return the next paced Mag7 option-snapshot session start in ET."""
        eastern = ZoneInfo(EASTERN_TZ)
        current = (now or datetime.now(eastern)).astimezone(eastern)
        candidate = current.replace(hour=8, minute=0, second=0, microsecond=0)
        if current >= candidate:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    @staticmethod
    def _is_oi_finder_mag7_live_session(now: datetime) -> bool:
        """Poll the paced Mag7 chain during premarket and the regular session."""
        current = now.astimezone(ZoneInfo(EASTERN_TZ))
        if current.weekday() >= 5:
            return False
        minute_of_day = current.hour * 60 + current.minute
        return 8 * 60 <= minute_of_day < 16 * 60 + 15

    # ------------------------------------------------------------------
    # MAG7 chart signal scanner (rebuilt 2026-08-10 after the backend loss).
    #
    # Reads ONLY the already-warm OI-finder chart cache — never starts a
    # broker fetch or a chart build — and replays the surviving signal
    # engines over it: scanner._tos_mtf_ema_signal_payload for the intraday
    # 4x8/9x20 families and ganesh_higher_timeframe_signals for the D..M
    # families. Memoized per (symbol, last bar time) so a dashboard poll on
    # an unchanged tape costs a dict lookup.
    # ------------------------------------------------------------------
    MAG7_SIGNAL_INTRADAY_TIMEFRAMES = ("1H", "2H", "4H")
    MAG7_SIGNAL_HIGHER_TIMEFRAMES = ("D", "2D", "3D", "4D", "W", "M")

    @staticmethod
    def _mag7_signal_session_window(session: str, now_et: datetime) -> tuple[datetime, datetime]:
        """Return the [start, end] ET window a session's signals must fall in."""
        if session == "premarket":
            # Prior 17:00 ET through today's 09:29 ET.
            end = now_et.replace(hour=9, minute=29, second=59, microsecond=0)
            start = (end - timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
            return start, end
        # Regular session 5m tape: 09:00 through the 15:30 candle.
        start = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        end = now_et.replace(hour=15, minute=30, second=59, microsecond=0)
        return start, end

    def _mag7_chart_signal_row(self, symbol: str, session: str, now_et: datetime) -> dict | None:
        """One scanner row from the cached chart tape, or None when cold."""
        with self.oi_finder_chart_lock:
            cached = self.oi_finder_chart_cache.get(symbol)
            payload = dict(cached["payload"]) if cached and cached.get("payload") else None
            history_ready = bool(cached and cached.get("history_ready"))
        bars = payload.get("bars") if payload else None
        if not bars:
            return None
        latest_bar_time = int(bars[-1].get("time") or 0)
        memo_key = (symbol, session, latest_bar_time)
        memo = getattr(self, "_mag7_chart_signal_memo", None)
        if not isinstance(memo, dict):
            memo = {}
            self._mag7_chart_signal_memo = memo
        if memo_key in memo:
            return memo[memo_key]

        study_bars = payload.get("studyBars") or bars
        daily_bars = payload.get("dailyBars") or []
        eastern = ZoneInfo(EASTERN_TZ)
        window_start, window_end = self._mag7_signal_session_window(session, now_et)

        def in_window(epoch: object) -> bool:
            try:
                stamp = datetime.fromtimestamp(int(epoch or 0), tz=timezone.utc).astimezone(eastern)
            except (TypeError, ValueError, OverflowError, OSError):
                return False
            return window_start <= stamp <= window_end

        # Intraday 4x8 / 9x20 families come from the chart's own MTF replay.
        intraday_signals = [
            signal for signal in (payload.get("mtfSignals") or [])
            if in_window(signal.get("time"))
            and str(signal.get("direction") or "").upper() == "CALL"
        ]
        # Higher-timeframe families replay here; the rebuilt chart payload
        # does not carry them. This replay is the dominant cost (~2s/symbol),
        # so it is memoized on the STUDY tape's identity and shared by both
        # sessions — the signal set is session-independent, only the window
        # filter below differs. Without this the same replay ran twice per
        # symbol and re-ran on every one-minute bar.
        ganesh_key = (
            symbol,
            int((study_bars[-1].get("time") if study_bars else 0) or 0),
            len(study_bars or ()),
            int((daily_bars[-1].get("time") if daily_bars else 0) or 0),
        )
        ganesh_memo = getattr(self, "_mag7_ganesh_memo", None)
        if not isinstance(ganesh_memo, dict):
            ganesh_memo = {}
            self._mag7_ganesh_memo = ganesh_memo
        ganesh_signals = ganesh_memo.get(ganesh_key)
        if ganesh_signals is None:
            try:
                ganesh_payload = build_ganesh_higher_timeframe_signal_payload(
                    study_bars,
                    bars,
                    daily_bars,
                )
                ganesh_signals = list(ganesh_payload.get("signals") or [])
            except Exception:
                ganesh_signals = []
            ganesh_memo[ganesh_key] = ganesh_signals
            if len(ganesh_memo) > 60:
                for stale in list(ganesh_memo)[: len(ganesh_memo) - 60]:
                    ganesh_memo.pop(stale, None)
        higher_signals = [
            signal for signal in ganesh_signals
            if in_window(signal.get("time"))
            and str(signal.get("direction") or "").upper() in {"CALL", "MACD"}
        ]

        def labels(signals: object, families: set[str]) -> list[str]:
            seen: list[str] = []
            for signal in signals if isinstance(signals, list) else []:
                if str(signal.get("family") or "") not in families:
                    continue
                label = str(signal.get("label") or "").strip()
                if label and label not in seen:
                    seen.append(label)
            return seen

        intraday_48 = labels(intraday_signals, {"4x8"})
        intraday_920 = labels(intraday_signals, {"9x20"})
        higher_48 = labels(higher_signals, {"ganesh48"})
        cyan = labels(higher_signals, {"ganesh920"})
        macd = labels(higher_signals, {"ganeshMacd"})

        all_signals = [*intraday_signals, *higher_signals]
        signal_times = sorted({int(signal.get("time") or 0) for signal in all_signals if signal.get("time")})
        signal_candles = [
            datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(eastern).strftime("%H:%M")
            for stamp in signal_times
        ]

        def pct_change(series: list[dict], minutes: int, field: str) -> float | None:
            if not series:
                return None
            cutoff = latest_bar_time - minutes * 60
            prior = [bar for bar in series if int(bar.get("time") or 0) <= cutoff]
            if not prior:
                return None
            first = float(prior[-1].get(field) or 0)
            last = float(series[-1].get(field) or 0)
            if first <= 0:
                return None
            return round((last - first) / first * 100, 2)

        four_hour_volume_change = pct_change(bars, 240, "volume")
        one_hour_close_change = pct_change(bars, 60, "close")
        # The TOS "all of" gate: a bullish intraday structure on the chart's
        # own MTF replay is what the old scanner gated on.
        gate_pass = bool(intraday_48 or intraday_920)

        row = {
            "symbol": symbol,
            "lastPrice": round(float(bars[-1].get("close") or 0), 2),
            "tosAllOfPass": gate_pass,
            "tosGateEvaluatedAt": now_et.isoformat(),
            "latestScanCandleAt": datetime.fromtimestamp(
                latest_bar_time, tz=timezone.utc
            ).astimezone(eastern).isoformat() if latest_bar_time else None,
            "fourHourVolumeChangePct": four_hour_volume_change,
            "oneHourCloseChangePct": one_hour_close_change,
            "intraday48Signals": intraday_48,
            "intraday920Signals": intraday_920,
            "higher48Signals": higher_48,
            "higher920Signals": cyan,
            "cyanSignals": cyan,
            "macdSignals": macd,
            "signals": [*intraday_48, *higher_48, *cyan, *macd],
            "signalCandles": signal_candles,
            "signalCount": len(all_signals),
            "firstSignalAt": signal_candles[0] if signal_candles else None,
            "latestSignalAt": signal_candles[-1] if signal_candles else None,
            "session": "premarket" if session == "premarket" else "regular",
            "historyReady": history_ready,
        }
        memo[memo_key] = row
        if len(memo) > 120:
            for stale in list(memo)[: len(memo) - 120]:
                memo.pop(stale, None)
        return row

    @staticmethod
    def _windowed_ganesh_chart_payload(payload: dict) -> dict:
        """Trim a payload's ganesh signals to its own visible bar window."""
        tape = payload.get("ganeshHigherTimeframeSignals")
        bars = payload.get("bars")
        if not isinstance(tape, dict) or not isinstance(bars, list) or not bars:
            return payload
        signals = tape.get("signals")
        if not isinstance(signals, list) or not signals:
            return payload
        try:
            first_time = int(bars[0].get("time") or 0)
        except (AttributeError, TypeError, ValueError):
            return payload
        if first_time <= 0:
            return payload
        visible = [row for row in signals if int((row or {}).get("time") or 0) >= first_time]
        if len(visible) == len(signals):
            return payload
        return {**payload, "ganeshHigherTimeframeSignals": {**tape, "signals": visible}}

    def _ganesh_signal_payload_for_chart(
        self,
        study_bars: list[dict],
        bars: list[dict],
        daily_bars: list[dict],
        symbol: str = "",
        first_chart_time: int = 0,
    ) -> dict:
        """Ganesh D..M payload for the chart, memoized on the study tape.

        Same cache the MAG7 scanner rows use: the replay is the dominant CPU
        cost (~2s/symbol) and its output depends only on the tapes, not on
        who asked, so chart requests and scanner passes share one result.
        """
        ganesh_key = (
            str(symbol or "").upper(),
            int((study_bars[-1].get("time") if study_bars else 0) or 0),
            len(study_bars or ()),
            int((daily_bars[-1].get("time") if daily_bars else 0) or 0),
        )
        memo = getattr(self, "_mag7_ganesh_memo", None)
        if not isinstance(memo, dict):
            memo = {}
            self._mag7_ganesh_memo = memo
        def windowed(signals: object) -> list[dict]:
            rows = list(signals or []) if isinstance(signals, list) else []
            if first_chart_time <= 0:
                return rows
            return [row for row in rows if int(row.get("time") or 0) >= first_chart_time]

        cached_signals = memo.get(ganesh_key)
        if cached_signals is not None:
            return {
                "schemaVersion": GANESH_SCHEMA_VERSION,
                "mode": GANESH_SIGNAL_MODE,
                "sourceAggregationMinutes": GANESH_SOURCE_AGGREGATION_MINUTES,
                "historyReady": True,
                "signals": windowed(cached_signals),
            }
        try:
            payload = build_ganesh_higher_timeframe_signal_payload(study_bars, bars, daily_bars)
        except Exception:
            return {"historyReady": False, "signals": []}
        if payload.get("historyReady") is True:
            # Memoize the FULL replay (the scanner tables need every signal),
            # but only ever ship the visible window to the browser.
            memo[ganesh_key] = list(payload.get("signals") or [])
            if len(memo) > 60:
                for stale in list(memo)[: len(memo) - 60]:
                    memo.pop(stale, None)
        return {**payload, "signals": windowed(payload.get("signals"))}

    def _mag7_chart_signals_snapshot(self, session: str) -> dict:
        """Last completed table for the request path; warming shell if none."""
        snapshot = getattr(self, "_mag7_chart_signal_snapshots", {}).get(session)
        if snapshot:
            return snapshot
        eastern = ZoneInfo(EASTERN_TZ)
        now_et = datetime.now(eastern)
        symbols = self._mag7_option_underlyings()
        is_premarket = session == "premarket"
        return {
            "status": "WARMING",
            "date": now_et.date().isoformat(),
            "timezone": EASTERN_TZ,
            "sessionLabel": (
                "4H chart session from prior 5:00 PM through 9:29 AM ET"
                if is_premarket
                else "5m chart signals from 9:00 AM through the 3:30 PM ET candle"
            ),
            "source": (
                "Charts & OI 4H premarket cutoff signal tape"
                if is_premarket
                else "Charts & OI 5-minute signal tapes"
            ),
            "sourceAggregationMinutes": 240 if is_premarket else 5,
            "bullishOnly": True,
            "logic": "ALL_TOS_GATES_AND_ANY_PANEL_SIGNAL",
            "allOfRules": [],
            "anyOfRules": [],
            "families": ["4x8", "ganesh48", "ganesh920", "ganeshMacd"],
            "timeframes": [*self.MAG7_SIGNAL_INTRADAY_TIMEFRAMES, *self.MAG7_SIGNAL_HIGHER_TIMEFRAMES],
            "intradayFamilies": ["4x8"] if is_premarket else ["4x8", "9x20"],
            "intradayTimeframes": list(self.MAG7_SIGNAL_INTRADAY_TIMEFRAMES),
            "higherFamilies": ["ganesh48", "ganesh920", "ganeshMacd"],
            "higherTimeframes": list(self.MAG7_SIGNAL_HIGHER_TIMEFRAMES),
            "symbols": symbols,
            "readySymbols": [],
            "pendingSymbols": symbols,
            "refreshingSymbols": [],
            "coverage": {
                "total": len(symbols), "ready": 0, "loading": len(symbols), "stale": 0,
                "refreshing": 0, "allOfPassed": 0, "allOfBlocked": 0, "allOfPending": len(symbols),
            },
            "allOfPassSymbols": [],
            "allOfBlockedSymbols": [],
            "allOfPendingSymbols": symbols,
            "rows": [],
            "matchCount": 0,
            "lastSignalAt": None,
            "refreshedAt": None,
            "generatedAt": now_et.isoformat(),
            "message": "Warming MAG7 chart tapes; signals appear as each chart caches.",
        }

    def mag7_chart_signals_payload(self, session: str) -> dict:
        """Dashboard payload for the MAG7 premarket / 5-minute signal tables."""
        eastern = ZoneInfo(EASTERN_TZ)
        now_et = datetime.now(eastern)
        symbols = self._mag7_option_underlyings()
        rows: list[dict] = []
        ready: list[str] = []
        pending: list[str] = []
        for symbol in symbols:
            try:
                row = self._mag7_chart_signal_row(symbol, session, now_et)
            except Exception:
                row = None
            if row is None:
                pending.append(symbol)
                continue
            ready.append(symbol)
            if row["signalCount"]:
                rows.append(row)
        rows.sort(key=lambda item: (
            not item["tosAllOfPass"],
            -int(item["signalCount"]),
            item["symbol"],
        ))
        passed = [row["symbol"] for row in rows if row["tosAllOfPass"]]
        blocked = [row["symbol"] for row in rows if not row["tosAllOfPass"]]
        is_premarket = session == "premarket"
        payload = {
            "status": "READY" if ready else "WARMING",
            "date": now_et.date().isoformat(),
            "timezone": EASTERN_TZ,
            "sessionLabel": (
                "4H chart session from prior 5:00 PM through 9:29 AM ET"
                if is_premarket
                else "5m chart signals from 9:00 AM through the 3:30 PM ET candle"
            ),
            "source": (
                "Charts & OI 4H premarket cutoff signal tape"
                if is_premarket
                else "Charts & OI 5-minute signal tapes"
            ),
            "sourceAggregationMinutes": 240 if is_premarket else 5,
            "bullishOnly": True,
            "logic": "ALL_TOS_GATES_AND_ANY_PANEL_SIGNAL",
            "allOfRules": [],
            "anyOfRules": [],
            "families": ["4x8", "ganesh48", "ganesh920", "ganeshMacd"],
            "timeframes": [*self.MAG7_SIGNAL_INTRADAY_TIMEFRAMES, *self.MAG7_SIGNAL_HIGHER_TIMEFRAMES],
            "intradayFamilies": ["4x8"] if is_premarket else ["4x8", "9x20"],
            "intradayTimeframes": list(self.MAG7_SIGNAL_INTRADAY_TIMEFRAMES),
            "higherFamilies": ["ganesh48", "ganesh920", "ganeshMacd"],
            "higherTimeframes": list(self.MAG7_SIGNAL_HIGHER_TIMEFRAMES),
            "symbols": symbols,
            "readySymbols": ready,
            "pendingSymbols": pending,
            "refreshingSymbols": [],
            "coverage": {
                "total": len(symbols),
                "ready": len(ready),
                "loading": len(pending),
                "stale": 0,
                "refreshing": 0,
                "allOfPassed": len(passed),
                "allOfBlocked": len(blocked),
                "allOfPending": len(pending),
            },
            "allOfPassSymbols": passed,
            "allOfBlockedSymbols": blocked,
            "allOfPendingSymbols": pending,
            "rows": rows,
            "matchCount": len(rows),
            "lastSignalAt": max(
                (row["latestSignalAt"] for row in rows if row["latestSignalAt"]),
                default=None,
            ),
            "refreshedAt": now_et.isoformat(),
            "generatedAt": now_et.isoformat(),
            "message": (
                f"{len(rows)} of {len(symbols)} MAG7 symbols show signals in this session."
                if ready
                else "Warming MAG7 chart tapes; signals appear as each chart caches."
            ),
        }
        snapshots = getattr(self, "_mag7_chart_signal_snapshots", None)
        if not isinstance(snapshots, dict):
            snapshots = {}
            self._mag7_chart_signal_snapshots = snapshots
        snapshots[session] = payload
        return payload

    def mag7_premarket_plan_payload(self) -> dict:
        """Build a read-only Mag7 plan from cached Finder chains only.

        This endpoint never starts broker requests.  The paced Mag7 collector
        owns all chain access, so opening the panel cannot turn into a burst of
        option-chain calls for the broader watchlist.
        """
        eastern = ZoneInfo(EASTERN_TZ)
        now = datetime.now(eastern)

        def number(value: object) -> float:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        def leader_and_totals(rows: object) -> dict:
            candidates = [
                row for row in (rows or [])
                if isinstance(row, dict) and 0 <= number(row.get("days_to_expiration")) <= 7
            ]
            if not candidates:
                candidates = [row for row in (rows or []) if isinstance(row, dict)]
            total_volume = sum(number(row.get("volume")) for row in candidates)
            total_oi = sum(number(row.get("open_interest")) for row in candidates)
            leading = max(
                candidates,
                key=lambda row: (
                    number(row.get("volume")),
                    number(row.get("open_interest")),
                    number(row.get("strength_score")),
                ),
                default=None,
            )
            return {
                "volume": round(total_volume),
                "openInterest": round(total_oi),
                "leader": None if leading is None else {
                    "strike": leading.get("strike"),
                    "expiry": leading.get("expiry"),
                    "dte": leading.get("days_to_expiration"),
                    "volume": round(number(leading.get("volume"))),
                    "openInterest": round(number(leading.get("open_interest"))),
                },
            }

        with self.oi_finder_lock:
            cached_chains = dict(self.oi_finder_cache)

        rows: list[dict] = []
        for symbol in self._mag7_option_underlyings():
            cached = cached_chains.get(symbol)
            if not cached:
                rows.append({
                    "symbol": symbol,
                    "available": False,
                    "read": "WAITING FOR FIRST SNAPSHOT",
                    "tone": "neutral",
                    "note": "The paced Mag7 collector has not saved this ticker yet.",
                })
                continue
            captured_at, payload = cached
            payload = payload or {}
            price = number(payload.get("underlyingPrice"))
            current_atm = payload.get("currentAtm") or {}
            expected_move = number(current_atm.get("expectedMove"))
            calls = leader_and_totals(payload.get("callRows"))
            puts = leader_and_totals(payload.get("putRows"))
            call_volume = calls["volume"]
            put_volume = puts["volume"]
            if call_volume > put_volume * 1.2 and call_volume > 0:
                read, tone, note = "CALL WATCH", "call", "Displayed OTM call volume leads puts in the nearby expiries."
            elif put_volume > call_volume * 1.2 and put_volume > 0:
                read, tone, note = "PUT WATCH", "put", "Displayed OTM put volume leads calls in the nearby expiries."
            else:
                read, tone, note = "MIXED / WAIT", "neutral", "Displayed call and put volume are close; do not force a directional read."
            captured_et = captured_at.astimezone(eastern)
            age_seconds = max(0, int((now - captured_et).total_seconds()))
            rows.append({
                "symbol": symbol,
                "available": price > 0,
                "price": price,
                "todayChange": number(payload.get("todayChange")),
                "todayChangePercent": number(payload.get("todayChangePercent")),
                "expectedMove": expected_move,
                "expectedLow": max(0, price - expected_move) if expected_move > 0 else None,
                "expectedHigh": price + expected_move if expected_move > 0 else None,
                "call": calls,
                "put": puts,
                "read": read,
                "tone": tone,
                "note": note,
                "source": payload.get("source") or "Latest option-chain snapshot",
                "scannedAt": _serialize_value(captured_et),
                "ageSeconds": age_seconds,
                "stale": age_seconds > max(self.oi_finder_mag7_live_interval_seconds * 2, 900),
            })

        available = sum(1 for row in rows if row.get("available"))
        return {
            "generatedAt": _serialize_value(now),
            "scope": "Saved MAG7 scanner watchlist only",
            "status": "LIVE" if available == len(rows) and rows else ("PARTIAL" if available else "WARMING"),
            "message": "Read-only plan from the latest paced Mag7 option-chain snapshots. It is context, not a trade instruction.",
            "collector": {
                "enabled": self.oi_finder_mag7_live_enabled,
                "status": self.oi_finder_mag7_live_status,
                "message": self.oi_finder_mag7_live_message,
                "intervalSeconds": self.oi_finder_mag7_live_interval_seconds,
                "lastRun": _serialize_value(self.oi_finder_mag7_live_last_run),
                "nextRun": _serialize_value(self.oi_finder_mag7_live_next_run),
                "progress": dict(self.oi_finder_mag7_live_progress),
            },
            "rows": rows,
        }

    def _start_oi_finder_mag7_live_collector(self) -> None:
        """Start the MAG7-only, paced live-volume collector once per boot."""
        if not self.oi_finder_mag7_live_enabled:
            return
        if self.oi_finder_mag7_live_thread is None or not self.oi_finder_mag7_live_thread.is_alive():
            self.oi_finder_mag7_live_thread = threading.Thread(
                target=self._oi_finder_mag7_live_collector_loop,
                name="oi-finder-mag7-live-volume",
                daemon=True,
            )
            self.oi_finder_mag7_live_thread.start()

    def _oi_finder_mag7_live_collector_loop(self) -> None:
        """Save live front-expiry volume buckets for the saved MAG7 list.

        One chain is fetched at a time with a pause between symbols. No ticker
        outside the saved MAG7 scanner list is requested here; the remaining
        watchlist stays on-demand through the Finder search box.
        """
        eastern = ZoneInfo(EASTERN_TZ)
        while self.oi_finder_mag7_live_enabled:
            # Visible chart and chain requests own the CPU/GIL. Pause this
            # background collector until the trader has been idle for 45s.
            while self.oi_finder_mag7_live_enabled and (
                time.monotonic() < float(getattr(self, "oi_finder_interactive_until", 0.0))
            ):
                self.oi_finder_mag7_live_status = "Yielding to active trader"
                self.oi_finder_mag7_live_message = "Live MAG7 snapshots are paused while charts or options are in use."
                self.oi_finder_mag7_live_next_run = datetime.now(eastern) + timedelta(seconds=1)
                time.sleep(1.0)
            now = datetime.now(eastern)
            if not self._is_oi_finder_mag7_live_session(now):
                self.oi_finder_mag7_live_status = "Waiting for premarket session"
                self.oi_finder_mag7_live_message = (
                    "MAG7 option snapshots resume at 8:00 AM ET. Other saved watchlist tickers remain search-only."
                )
                self.oi_finder_mag7_live_next_run = self._next_oi_finder_mag7_live_session(now)
                time.sleep(30)
                continue

            symbols = self._mag7_option_underlyings()
            self.oi_finder_mag7_live_status = "Saving Mag7 option snapshots"
            self.oi_finder_mag7_live_last_run = now
            self.oi_finder_mag7_live_progress = {"completed": 0, "total": len(symbols), "failed": 0}
            failures: list[str] = []
            cycle_started = time.monotonic()
            for index, symbol in enumerate(symbols, start=1):
                if not self.oi_finder_mag7_live_enabled:
                    break
                while self.oi_finder_mag7_live_enabled and (
                    time.monotonic() < float(getattr(self, "oi_finder_interactive_until", 0.0))
                ):
                    self.oi_finder_mag7_live_status = "Yielding to active trader"
                    self.oi_finder_mag7_live_message = "Live MAG7 snapshots are paused while charts or options are in use."
                    self.oi_finder_mag7_live_next_run = datetime.now(eastern) + timedelta(seconds=1)
                    time.sleep(1.0)
                if not self.oi_finder_mag7_live_enabled:
                    break
                try:
                    payload = self.oi_finder_payload(symbol, force=True, background_snapshot=True)
                    if not bool(payload.get("live")):
                        error = (payload.get("errors") or [{}])[0].get("error") or "No live option chain returned."
                        raise RuntimeError(str(error))
                except Exception as exc:
                    failures.append(f"{symbol}: {exc}")
                    self.oi_finder_mag7_live_last_error = failures[-1]
                self.oi_finder_mag7_live_progress = {
                    "completed": index,
                    "total": len(symbols),
                    "failed": len(failures),
                }
                self.oi_finder_mag7_live_message = (
                    f"Saving Mag7 option snapshots: {index}/{len(symbols)} tickers "
                    f"({len(failures)} unavailable)."
                )
                if index < len(symbols):
                    time.sleep(self.oi_finder_mag7_live_symbol_pause_seconds)

            finished = datetime.now(eastern)
            elapsed = time.monotonic() - cycle_started
            wait_seconds = max(30.0, self.oi_finder_mag7_live_interval_seconds - elapsed)
            self.oi_finder_mag7_live_last_run = finished
            self.oi_finder_mag7_live_next_run = finished + timedelta(seconds=wait_seconds)
            if failures:
                self.oi_finder_mag7_live_status = "Live collection partial"
                self.oi_finder_mag7_live_message = (
                    f"Saved {len(symbols) - len(failures)}/{len(symbols)} Mag7 option snapshots. "
                    "Unavailable symbols retry on the next two-minute pass."
                )
            else:
                self.oi_finder_mag7_live_status = "Live collection active"
                self.oi_finder_mag7_live_message = (
                    f"Saved {len(symbols)} Mag7 option snapshots. "
                    "The remaining saved watchlist is collected only when searched."
                )
                self.oi_finder_mag7_live_last_error = ""
            time.sleep(wait_seconds)

    def _start_oi_finder_snapshot_schedule(self) -> None:
        """Start the slow, after-close OI Finder archive worker once per boot."""
        if not self.oi_finder_snapshot_enabled:
            return
        if self.oi_finder_snapshot_thread is None or not self.oi_finder_snapshot_thread.is_alive():
            self.oi_finder_snapshot_thread = threading.Thread(
                target=self._oi_finder_daily_snapshot_loop,
                name="oi-finder-daily-history",
                daemon=True,
            )
            self.oi_finder_snapshot_thread.start()

    def _oi_finder_daily_snapshot_loop(self) -> None:
        """Persist one end-of-day 0-14 DTE option-chain reading per watchlist ticker.

        This is deliberately not part of the Mag7 scanner.  It runs after the
        close, one ticker at a time, and waits at least ten seconds between
        chains so the history collector cannot turn into a request burst.
        """
        eastern = ZoneInfo(EASTERN_TZ)
        while self.oi_finder_snapshot_enabled:
            now = datetime.now(eastern)
            target = now.replace(
                hour=self.oi_finder_snapshot_hour_et,
                minute=self.oi_finder_snapshot_minute_et,
                second=0,
                microsecond=0,
            )
            if now.weekday() >= 5 or now < target:
                self.oi_finder_snapshot_status = "Scheduled"
                self.oi_finder_snapshot_message = "Waiting for the next weekday after-close history snapshot."
                self.oi_finder_snapshot_next_run = self._next_oi_finder_snapshot_time(now)
                time.sleep(60)
                continue

            today = now.date().isoformat()
            persisted = self.repository.get_app_settings()
            if persisted.get("oi_finder_snapshot_last_attempt_date") == today:
                self.oi_finder_snapshot_status = "Complete"
                self.oi_finder_snapshot_message = "Today's after-close history pass has already run."
                self.oi_finder_snapshot_next_run = self._next_oi_finder_snapshot_time(now)
                time.sleep(60)
                continue

            symbols = self._normalize_option_watchlist(settings.scanner.default_universe)
            self.oi_finder_snapshot_status = "Saving daily history"
            self.oi_finder_snapshot_message = (
                f"Saving 0-31 DTE option-chain history for {len(symbols)} saved watchlist tickers, one at a time."
            )
            self.oi_finder_snapshot_last_run = now
            self.oi_finder_snapshot_progress = {"completed": 0, "total": len(symbols), "failed": 0}
            failures: list[str] = []
            for index, symbol in enumerate(symbols, start=1):
                if not self.oi_finder_snapshot_enabled:
                    break
                try:
                    payload = self.oi_finder_payload(symbol, force=True, background_snapshot=True)
                    if not bool(payload.get("live")):
                        raise RuntimeError(str((payload.get("errors") or [{}])[0].get("error") or "No live option chain returned."))
                except Exception as exc:
                    failures.append(f"{symbol}: {exc}")
                    self.oi_finder_snapshot_last_error = failures[-1]
                self.oi_finder_snapshot_progress = {
                    "completed": index,
                    "total": len(symbols),
                    "failed": len(failures),
                }
                self.oi_finder_snapshot_message = (
                    f"Saving daily 0-31 DTE history: {index}/{len(symbols)} tickers "
                    f"({len(failures)} unavailable)."
                )
                # Do not reduce this to a rapid scanner cadence.  One option
                # chain plus quote request per ticker is intentionally paced.
                if index < len(symbols):
                    time.sleep(self.oi_finder_snapshot_interval_seconds)

            self.repository.set_app_setting("oi_finder_snapshot_last_attempt_date", today)
            self.repository.set_app_setting("oi_finder_snapshot_last_attempt_count", str(len(symbols)))
            self.repository.set_app_setting("oi_finder_snapshot_last_failure_count", str(len(failures)))
            self.oi_finder_snapshot_last_run = datetime.now(eastern)
            self.oi_finder_snapshot_next_run = self._next_oi_finder_snapshot_time(self.oi_finder_snapshot_last_run)
            if failures:
                self.oi_finder_snapshot_status = "Complete with unavailable tickers"
                self.oi_finder_snapshot_message = (
                    f"Daily history pass finished: {len(symbols) - len(failures)}/{len(symbols)} saved. "
                    "Unavailable tickers will be retried on the next trading day."
                )
                self.repository.log_bot_event("oi_finder_history_partial", self.oi_finder_snapshot_message)
            else:
                self.oi_finder_snapshot_status = "Complete"
                self.oi_finder_snapshot_message = (
                    f"Daily history pass finished: {len(symbols)} saved option chains across 0-31 DTE."
                )
                self.oi_finder_snapshot_last_error = ""
                self.repository.log_bot_event("oi_finder_history_complete", self.oi_finder_snapshot_message)
            time.sleep(60)

    def _update_oi_scanner_auto_summary(self) -> None:
        if not self.oi_scanner_auto_enabled:
            self.oi_mag7_auto_status = "Stopped"
            self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
            self.oi_scanner_auto_status = "Stopped"
            self.oi_scanner_auto_message = (
                "MAG7-only OI scanner is stopped. "
                "OI Finder remains available for manual ticker searches."
            )
            self.oi_scanner_auto_next_run = None
            return
        status = str(self.oi_mag7_auto_status)
        if status.startswith("Error"):
            self.oi_scanner_auto_status = "Error"
        elif status.startswith("Scanning"):
            self.oi_scanner_auto_status = "Scanning"
        elif status.startswith("Sleeping") or status.startswith("Running full list"):
            self.oi_scanner_auto_status = "Sleeping"
        else:
            self.oi_scanner_auto_status = "Starting"
        self.oi_scanner_auto_last_run = self.oi_mag7_auto_last_run
        self.oi_scanner_auto_next_run = self.oi_mag7_auto_next_run
        self.oi_scanner_auto_last_error = self.oi_mag7_auto_last_error
        self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
        self.oi_watchlist_auto_next_run = None
        self.oi_scanner_auto_message = (
            f"MAG7 {self.oi_mag7_auto_status} | direct Schwab/TOS option chains | "
            f"Watchlist OI disabled"
        )

    def _quote_priority_split(self, symbols: list[str]) -> tuple[list[str], list[str]]:
        """Put live long-side movers first without removing any symbol from coverage."""
        universe = _dedupe_symbol_tokens(symbols)
        scanner = getattr(self, "scanner", None)
        get_quotes = getattr(getattr(scanner, "client", None), "get_quotes", None)
        if not universe or not callable(get_quotes):
            return [], universe
        try:
            quote_map = get_quotes(universe) or {}
        except Exception:
            return [], universe
        movers: list[tuple[str, float, float]] = []
        for symbol in universe:
            quote = quote_map.get(symbol) or {}
            try:
                price = float(quote.get("last_price") or 0.0)
                change_pct = float(quote.get("change_pct") or 0.0)
                volume = float(quote.get("volume") or 0.0)
            except (TypeError, ValueError):
                continue
            if (
                price >= float(settings.scanner.min_price)
                and change_pct >= float(settings.scanner.fast_quote_prefilter_min_change_pct)
            ):
                movers.append((symbol, change_pct, volume))
        movers.sort(key=lambda item: (-item[1], -item[2], item[0]))
        mover_symbols = [item[0] for item in movers]
        mover_set = set(mover_symbols)
        return mover_symbols, [symbol for symbol in universe if symbol not in mover_set]

    def _quote_priority_order(self, symbols: list[str]) -> list[str]:
        movers, remaining = self._quote_priority_split(symbols)
        return [*movers, *remaining]

    def _next_stock_scanner_auto_batch(self) -> dict:
        mag7 = set(self._normalize_option_watchlist(self.mag7_scanner_watchlist))
        universe = [
            symbol for symbol in _dedupe_symbol_tokens(settings.scanner.default_universe)
            if symbol not in mag7
        ]
        if not universe:
            return {"symbols": [], "hotSymbols": [], "cursor": 0, "universeCount": 0}
        movers, remaining = self._quote_priority_split(universe)
        hot = movers[: min(self.scanner_auto_hot_lane_size, self.scanner_auto_deep_batch_size)]
        hot_set = set(hot)
        rotation_pool = [symbol for symbol in [*movers, *remaining] if symbol not in hot_set]
        breadth_slots = max(self.scanner_auto_deep_batch_size - len(hot), 0)
        breadth: list[str] = []
        if breadth_slots and rotation_pool:
            cursor = int(self.scanner_auto_watchlist_cursor or 0) % len(rotation_pool)
            take = min(breadth_slots, len(rotation_pool))
            breadth = [rotation_pool[(cursor + offset) % len(rotation_pool)] for offset in range(take)]
            next_cursor = (cursor + take) % len(rotation_pool)
            if take and next_cursor <= cursor:
                self.scanner_auto_watchlist_completed_cycles += 1
            self.scanner_auto_watchlist_batch_start = cursor
            self.scanner_auto_watchlist_batch_end = cursor + take
            self.scanner_auto_watchlist_cursor = next_cursor
        else:
            self.scanner_auto_watchlist_batch_start = 0
            self.scanner_auto_watchlist_batch_end = 0
            self.scanner_auto_watchlist_cursor = 0
        batch = _dedupe_symbol_tokens([*hot, *breadth])
        return {
            "symbols": batch,
            "hotSymbols": hot,
            "cursor": self.scanner_auto_watchlist_cursor,
            "universeCount": len(universe),
        }

    def _scanner_auto_loop(self) -> None:
        time.sleep(5)
        while self.scanner_auto_enabled:
            cycle_started = time.monotonic()
            started_at = datetime.now().astimezone()
            self.scanner_auto_last_run = started_at
            self.scanner_auto_status = "Scanning"
            self.scanner_auto_message = "Auto scanner refreshing MAG7 plus a mover-first rotating deep batch."
            try:
                mag7_payload = self.scan(
                    symbols=self.mag7_scanner_watchlist,
                    scan_label="MAG7-Watchlist Options",
                    return_payload=False,
                )
                batch = self._next_stock_scanner_auto_batch()
                watchlist_payload = self.scan(
                    symbols=batch["symbols"],
                    scan_label="Watchlist Auto Batch",
                    return_payload=False,
                    merge_results=True,
                ) if batch["symbols"] else {"resultCount": 0, "activeResultCount": len(self.scan_results)}
                self.scanner_auto_last_error = ""
                self.scanner_auto_status = "Sleeping"
                self.scanner_auto_message = (
                    f"Auto scanner updated MAG7 ({int(mag7_payload.get('resultCount') or 0)}) "
                    f"and {len(batch['symbols'])}-symbol mover-first batch "
                    f"({len(batch['hotSymbols'])} hot, {int(watchlist_payload.get('resultCount') or 0)} new matches, "
                    f"{int(watchlist_payload.get('activeResultCount') or 0)} active)."
                )
            except Exception as exc:
                self.scanner_auto_status = "Error"
                self.scanner_auto_last_error = str(exc)
                self.scanner_auto_message = f"Auto scanner error: {exc}"
                self.repository.log_bot_event("scanner_auto_error", self.scanner_auto_message)

            remaining_seconds = max(
                float(self.scanner_auto_interval_seconds) - (time.monotonic() - cycle_started),
                0.0,
            )
            self.scanner_auto_next_run = datetime.now().astimezone() + timedelta(seconds=remaining_seconds)
            deadline = time.monotonic() + remaining_seconds
            while self.scanner_auto_enabled and time.monotonic() < deadline:
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))

    def _oi_scanner_auto_loop(self, universe: str) -> None:
        if universe != "mag7":
            self.oi_watchlist_auto_status = "Disabled (MAG7 only)"
            self.oi_watchlist_auto_next_run = None
            return
        time.sleep(5)
        while self.oi_scanner_auto_enabled:
            now = datetime.now().astimezone()
            is_mag7 = universe == "mag7"
            label = "MAG7 OI Scanner" if is_mag7 else "Watchlist OI Scanner"
            manual_priority = self._oi_manual_priority_event(label)
            while self.oi_scanner_auto_enabled and manual_priority.is_set():
                if is_mag7:
                    self.oi_mag7_auto_status = "Yielding to manual priority"
                else:
                    self.oi_watchlist_auto_status = "Yielding to manual priority"
                self._update_oi_scanner_auto_summary()
                time.sleep(0.1)
            # An actively browsing trader outranks the background scan: every
            # chart/chain payload request extends this window by 45s, and the
            # scan engine's MTF replays otherwise starve those requests for
            # the GIL (measured 10-16s chain responses while scanning).
            while self.oi_scanner_auto_enabled and (
                time.monotonic() < float(getattr(self, "oi_finder_interactive_until", 0.0))
            ):
                if is_mag7:
                    self.oi_mag7_auto_status = "Yielding to active trader"
                else:
                    self.oi_watchlist_auto_status = "Yielding to active trader"
                self._update_oi_scanner_auto_summary()
                time.sleep(1.0)
            symbols = self._mag7_oi_underlyings()
            if is_mag7:
                self.oi_mag7_auto_last_run = now
                self.oi_mag7_auto_status = "Scanning"
            else:
                self.oi_watchlist_auto_last_run = now
                self.oi_watchlist_auto_status = "Scanning"
            self._update_oi_scanner_auto_summary()
            try:
                payload = (
                    self.scan_oi_watchlist(
                        symbols=symbols,
                        scan_label=label,
                        return_payload=False,
                        merge_results=False,
                    )
                    if is_mag7
                    else self._execute_parallel_watchlist_oi_cycle(symbols)
                )
                result_count = int(payload.get("resultCount") or 0)
                if is_mag7:
                    self.oi_mag7_auto_last_error = ""
                    self.oi_mag7_auto_status = f"Running full list ({result_count})"
                else:
                    self.oi_watchlist_auto_last_error = ""
                    self.oi_watchlist_auto_status = (
                        f"Full cycle {self.oi_watchlist_universe_count} symbols / "
                        f"{self.oi_watchlist_worker_count} workers ({result_count} matches)"
                    )
            except Exception as exc:
                if is_mag7:
                    self.oi_mag7_auto_status = "Error"
                    self.oi_mag7_auto_last_error = str(exc)
                else:
                    self.oi_watchlist_auto_status = "Error"
                    self.oi_watchlist_auto_last_error = str(exc)
                self.repository.log_bot_event("oi_scanner_auto_error", f"{label} error: {exc}")

            remaining_seconds = (
                self.oi_mag7_continuous_pause_seconds
                if is_mag7
                else self.oi_watchlist_continuous_pause_seconds
            )
            next_run = datetime.now().astimezone() + timedelta(seconds=remaining_seconds)
            if is_mag7:
                self.oi_mag7_auto_next_run = next_run
            else:
                self.oi_watchlist_auto_next_run = next_run
            self._update_oi_scanner_auto_summary()
            deadline = time.monotonic() + remaining_seconds
            while self.oi_scanner_auto_enabled and time.monotonic() < deadline:
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))

    def _execute_parallel_watchlist_oi_cycle(self, symbols: list[str]) -> dict:
        universe = self._normalize_option_watchlist(symbols)
        # A single batched quote pass puts current movers in the first worker
        # batches. Every symbol remains in the list exactly once, so speed does
        # not trade away breadth.
        universe = self._quote_priority_order(universe)
        # Quote prioritization is real forward progress and can span several
        # provider chunks before worker futures are submitted. Give the first
        # worker batch a fresh watchdog window without using a timer heartbeat
        # that could conceal a blocked provider call.
        self.oi_watchlist_auto_last_run = datetime.now().astimezone()
        batches = [
            universe[index:index + self.oi_watchlist_batch_size]
            for index in range(0, len(universe), self.oi_watchlist_batch_size)
        ]
        if not batches:
            return {"resultCount": 0, "symbolCount": 0, "errors": []}
        started = time.monotonic()
        self.oi_watchlist_cycle_started_at = datetime.now().astimezone()
        self.oi_watchlist_batch_count = len(batches)
        self.oi_watchlist_batches_completed = 0
        self.oi_watchlist_batch_start = 0
        self.oi_watchlist_batch_end = 0
        errors: list[dict] = []
        with self.oi_watchlist_scan_lock:
            with ThreadPoolExecutor(
                max_workers=min(self.oi_watchlist_worker_count, len(batches)),
                thread_name_prefix="watchlist-oi-worker",
            ) as executor:
                futures = {
                    executor.submit(
                        self._execute_oi_scan,
                        symbols=batch,
                        scan_label="Watchlist OI Scanner",
                        event_name="oi_scan_parallel_batch",
                        merge_results=True,
                    ): (index, batch)
                    for index, batch in enumerate(batches)
                }
                for future in as_completed(futures):
                    index, batch = futures[future]
                    try:
                        payload = future.result()
                        errors.extend(payload.get("errors") or [])
                    except Exception as exc:
                        errors.append({"batch": index + 1, "error": str(exc)})
                    # This timestamp is the watchdog's progress heartbeat, not
                    # merely the full-cycle start time. A 397-symbol pass can
                    # legitimately take several minutes, while completed
                    # batches prove that the worker is still making progress.
                    # If no batch completes, the timestamp stops advancing and
                    # the watchdog can still detect a genuinely stuck scan.
                    self.oi_watchlist_auto_last_run = datetime.now().astimezone()
                    self.oi_watchlist_batches_completed += 1
                    self.oi_watchlist_batch_start = min(index * self.oi_watchlist_batch_size, len(universe))
                    self.oi_watchlist_batch_end = min(
                        self.oi_watchlist_batch_start + len(batch),
                        len(universe),
                    )
                    self._update_oi_scanner_auto_summary()
        self.oi_watchlist_completed_cycles += 1
        self.oi_watchlist_batch_cursor = 0
        self.oi_watchlist_batch_start = 0
        self.oi_watchlist_batch_end = len(universe)
        self.oi_watchlist_cycle_duration_seconds = round(time.monotonic() - started, 2)
        return {
            "resultCount": len(self.oi_watchlist_scan_results.index),
            "symbolCount": len(universe),
            "errors": errors,
            "workers": self.oi_watchlist_worker_count,
            "batches": len(batches),
            "durationSeconds": self.oi_watchlist_cycle_duration_seconds,
        }

    def _option_scheduler_loop(self) -> None:
        while self.option_bot_state == "Running":
            self.option_scan_wakeup.wait(timeout=max(5, int(self.option_scheduler_interval_seconds or 5)))
            self.option_scan_wakeup.clear()
            if self.option_bot_state != "Running":
                break
            self.option_scheduler_last_run = datetime.now().astimezone()
            try:
                # The scheduler only needs execution side effects. Building the
                # full dashboard payload here performs unrelated broker/data
                # reads and can delay the next option cycle for minutes.
                self.scan_options(create_plans=True, return_payload=False)
            except Exception as exc:
                self.option_bot_message = f"Option Alpaca engine error: {exc}"
                self.scheduler_last_error = str(exc)
                self.repository.log_bot_event("option_scheduler_error", self.option_bot_message)

    def _scheduler_loop(self) -> None:
        while self.scheduler_enabled:
            self.scheduler_last_run = datetime.now().astimezone()
            self.scheduler_cycle_count += 1
            self.scheduler_cycle_status = "Scanning"
            self.scheduler_cycle_message = "Syncing positions and evaluating the watchlist."
            try:
                self.scheduler_cycle_status = "Managing"
                self.scheduler_cycle_message = "Syncing orders and managing open paper positions."
                management_errors: list[str] = []
                for event_name, action in (
                    ("stock_scheduler_sync_error", self.trader.sync_order_statuses),
                    ("stock_scheduler_manage_error", self.trader.manage_open_trades),
                ):
                    try:
                        action()
                    except Exception as exc:
                        management_errors.append(str(exc))
                        try:
                            self.repository.log_bot_event(event_name, str(exc))
                        except Exception:
                            pass
                self.scheduler_last_error = " | ".join(management_errors)
                session_status = self._session_status(self.client.get_clock())
                if self._stock_auto_entries_allowed(session_status):
                    self.scheduler_cycle_status = "Executing"
                    self.scheduler_cycle_message = (
                        f"Auto-trading approved setups during {session_status['currentSession']}."
                    )
                    # The scheduler consumes only the execution result. Avoid
                    # unrelated dashboard broker/data reads on this hot path.
                    execution_result = self.execute_all_trades(return_payload=False)
                    trade_result = execution_result.get("result", {})
                    if trade_result.get("status") == "blocked":
                        blocked_message = str(trade_result.get("message") or "New entries blocked. Managing existing trades only.")
                        if "insufficient buying power" in blocked_message.lower():
                            self.scheduler_cycle_status = "Managing"
                            self.scheduler_cycle_message = blocked_message
                        elif "daily trade capital reached" in blocked_message.lower():
                            self.scheduler_cycle_status = "Managing"
                            self.scheduler_cycle_message = blocked_message
                        elif "per-trade amount is larger than the daily trade capital" in blocked_message.lower():
                            self.scheduler_cycle_status = "Monitoring"
                            self.scheduler_cycle_message = blocked_message
                        elif "smaller than share price" in blocked_message.lower():
                            self.scheduler_cycle_status = "Monitoring"
                            self.scheduler_cycle_message = blocked_message
                else:
                    # Closed/non-entry cycles still refresh signals, but the
                    # scheduler does not need a dashboard response.
                    self.scan(return_payload=False)
                    self.scheduler_cycle_status = "Monitoring"
                    self.scheduler_cycle_message = session_status["executionNote"]
                    self.repository.log_bot_event("scheduler_mode", f"Scheduler in {session_status['automationMode'].lower()}: {session_status['executionNote']}")
            except Exception as exc:
                self.scheduler_cycle_status = "Error"
                self.scheduler_cycle_message = str(exc)
                self.scheduler_last_error = str(exc)
                self.repository.log_bot_event("scheduler_error", str(exc))
            # Fire-and-forget after execution-critical work. The rotating
            # 40-symbol batches eventually cover the full watchlist, while the
            # overlap lock prevents news requests from stacking up.
            self._schedule_catalyst_information_refresh()
            self.scheduler_next_run = datetime.now().astimezone() + timedelta(seconds=self.scheduler_interval_seconds)
            if self.scheduler_cycle_status != "Error":
                self.scheduler_cycle_status = "Sleeping"
                self.scheduler_cycle_message = (
                    f"Waiting up to {self.scheduler_interval_seconds} seconds; fresh A+ HOT or A ACTIVE OI wakes execution immediately."
                )
            stock_entry_wakeup = getattr(self, "stock_entry_wakeup", None)
            if stock_entry_wakeup is None:
                stock_entry_wakeup = threading.Event()
                self.stock_entry_wakeup = stock_entry_wakeup
            stock_entry_wakeup.wait(timeout=max(float(self.scheduler_interval_seconds), 0.0))
            stock_entry_wakeup.clear()
        if not self.scheduler_enabled:
            self.scheduler_cycle_status = "Paused" if self.bot_state == "Paused" else "Stopped"
            self.scheduler_cycle_message = f"Automation loop is {self.scheduler_cycle_status.lower()}."

    def _stock_auto_entries_allowed(self, session_status: dict) -> bool:
        return self.bot_state == "Running" and bool(session_status.get("canAutoTrade"))

    def _dashboard_payload_minimal(self) -> dict:
        return {
            "status": {
                "marketStatus": "Loading",
                "sessionStatus": {
                    "core": "Unknown",
                    "extended": "Unknown",
                    "overnight": "Unknown",
                    "crypto": "Unknown",
                    "currentSession": "Loading",
                    "canAutoTrade": False,
                    "automationMode": "Loading",
                    "executionNote": "",
                },
                "clockTime": None,
                "nextOpen": None,
                "nextClose": None,
                "accountEquity": 0.0,
                "cash": 0.0,
                "lastEquity": 0.0,
                "dailyChange": 0.0,
                "dailyChangePct": 0.0,
                "buyingPower": 0.0,
                "dailyPnL": 0.0,
                "tradesToday": 0,
                "openPositions": 0,
                "openOrders": 0,
                "accountMode": "",
                "accountLabel": self.client.credentials.label,
                "activeAccountId": self.client.credentials.profile_id,
                "scanThreshold": settings.scanner.score_threshold,
                "aiThreshold": settings.ai.min_trade_score,
                "lastRefresh": _serialize_value(self.scan_timestamp),
                "databasePath": str(DATABASE_PATH),
                "watchlistCount": len(settings.scanner.default_universe),
            },
            "botState": self.bot_state,
            "optionBot": {
                "state": self.option_bot_state,
                "message": self.option_bot_message,
                "executionMode": self.option_bot_config["approvalMode"],
                "watchlistSource": self._option_watchlist_source(),
                "watchlistLabel": self._option_watchlist_source_label(),
                "tradeUniverseSource": self._option_watchlist_source(),
                "watchlistCount": len(self._option_bot_trade_universe()),
            },
            "learning": self._learning_status_payload(),
            "actionMessage": self.action_message,
            "oiActionMessage": self.oi_action_message,
            "scanJob": self.scan_job,
            "oiScanJob": self.oi_scan_job,
            "runtimeHealth": self._runtime_health_payload(),
            "scannerAuto": {
                "enabled": self.scanner_auto_enabled,
                "engine": "stock-scanner-engine",
                "mode": "mover_first_rotating_deep_batches",
                "intervalSeconds": self.scanner_auto_interval_seconds,
                "deepBatchSize": self.scanner_auto_deep_batch_size,
                "hotLaneSize": self.scanner_auto_hot_lane_size,
                "watchlistCursor": self.scanner_auto_watchlist_cursor,
                "watchlistBatchStart": self.scanner_auto_watchlist_batch_start,
                "watchlistBatchEnd": self.scanner_auto_watchlist_batch_end,
                "watchlistCompletedCycles": self.scanner_auto_watchlist_completed_cycles,
                "status": self.scanner_auto_status,
                "message": self.scanner_auto_message,
                "lastRun": _serialize_value(self.scanner_auto_last_run),
                "nextRun": _serialize_value(self.scanner_auto_next_run),
                "lastError": self.scanner_auto_last_error,
            },
            "stockPositionManager": {
                "engine": "stock-position-manager-engine",
                "intervalSeconds": self.stock_position_manager_interval_seconds,
                "status": self.stock_position_manager_status,
                "lastRun": _serialize_value(self.stock_position_manager_last_run),
                "lastError": self.stock_position_manager_last_error,
            },
            "scheduler": {
                "enabled": self.scheduler_enabled,
                "intervalSeconds": self.scheduler_interval_seconds,
                "lastRun": _serialize_value(self.scheduler_last_run),
                "nextRun": _serialize_value(self.scheduler_next_run),
                "cycleCount": self.scheduler_cycle_count,
                "status": self.scheduler_cycle_status,
                "message": self.scheduler_cycle_message,
                "lastError": self.scheduler_last_error,
                "entryBlockStatus": self.entry_block_status,
                "entryBlockMessage": self.entry_block_message,
            },
            "premarketPlan": self.premarket_plan_payload(refresh=False),
            "mag7PremarketPlan": self.mag7_premarket_plan_payload(),
            # The minimal payload is the REQUEST path: it must never replay
            # signals (a full build is ~20s). Serve the last completed tables
            # from the full builder, or a warming shell before the first one.
            "mag7PremarketChartSignals": self._mag7_chart_signals_snapshot("premarket"),
            "mag7FiveMinuteChartSignals": self._mag7_chart_signals_snapshot("regular"),
            "stockAutoReadiness": {
                "symbols": [],
                "thresholds": {},
                "rows": [],
                "session": "Loading",
                "mode": "warming",
                "requiresFreshOi": True,
                "tradeOverrides": {},
                "message": "Live stock readiness is refreshing in the background.",
            },
            "oiScannerAuto": {
                "enabled": self.oi_scanner_auto_enabled,
                "scope": "MAG7 watchlist",
                "symbols": self._mag7_oi_underlyings(),
                "optionChainSource": "Schwab/TOS option chain",
                "watchlistEnabled": False,
                "mag7Engine": "mag7-oi-scanner-engine",
                "mag7Mode": "continuous_full_list",
                "watchlistEngine": "disabled",
                "watchlistMode": "disabled",
                "watchlistWorkerCount": 0,
                "watchlistBatchSize": self.oi_watchlist_batch_size,
                "watchlistBatchCount": self.oi_watchlist_batch_count,
                "watchlistBatchesCompleted": self.oi_watchlist_batches_completed,
                "watchlistBatchStart": self.oi_watchlist_batch_start,
                "watchlistBatchEnd": self.oi_watchlist_batch_end,
                "watchlistUniverseCount": self.oi_watchlist_universe_count,
                "watchlistCompletedCycles": self.oi_watchlist_completed_cycles,
                "watchlistCycleStartedAt": _serialize_value(self.oi_watchlist_cycle_started_at),
                "watchlistCycleDurationSeconds": self.oi_watchlist_cycle_duration_seconds,
                "intervalSeconds": self.oi_scanner_auto_interval_seconds,
                "mag7IntervalSeconds": self.oi_mag7_auto_interval_seconds,
                "watchlistIntervalSeconds": None,
                "status": self.oi_scanner_auto_status,
                "message": self.oi_scanner_auto_message,
                "lastRun": _serialize_value(self.oi_scanner_auto_last_run),
                "nextRun": _serialize_value(self.oi_scanner_auto_next_run),
                "lastError": self.oi_scanner_auto_last_error,
            },
            "oiScannerRules": {
                "minPrice": 3.0,
                "minOneHourCloseChangePct": float(settings.scanner.min_one_hour_close_change_pct),
                "minFourHourVolumeChangePct": float(settings.scanner.min_four_hour_volume_change_pct),
                "signalLookbackBars": 2,
                "extendedHours": True,
                "liveCandle": True,
                "liveFiveMinuteEmaVwapRequired": False,
                "minTosRvolAnyTimeframe": float(settings.scanner.tos_rvol_num_dev),
                "minTosRvolMag7AnyTimeframe": float(settings.scanner.tos_rvol_mag7_num_dev),
                "minTosRvolMidTimeframe": float(settings.scanner.tos_rvol_mid_timeframe_num_dev),
                "minTosRvolFiveMinuteEarly": float(settings.scanner.tos_rvol_five_min_early_num_dev),
                "negativeRvolVetoTimeframes": list(self.scanner.TOS_RVOL_NEGATIVE_VETO_TIMEFRAMES),
                "requireMtfGate": False,
                "requireOneHourCloseGate": False,
                "requireFourHourVolumeGate": False,
                "minDelta": 0.20,
                "minExpectedMove": 2.0,
                "maxDaysToExpiration": OI_SCANNER_MAX_DAYS_TO_EXPIRATION,
                "allowZeroDteAfterHours": False,
                "allowZeroDteCore": True,
                "allowedSetups": sorted(OPTION_ALLOWED_SETUPS),
                "runUniverse": "MAG7 watchlist",
                "optionChainSource": "Schwab/TOS option chain",
                "watchlistExcludingMag7Count": 0,
            },
            "watchlist": settings.scanner.default_universe,
            "optionWatchlist": self.option_watchlist,
            "activeOptionWatchlist": self._active_option_watchlist(),
            "mag7OptionWatchlist": self._mag7_oi_underlyings(),
            "mag7OptionWatchlistSource": self.mag7_scanner_watchlist,
            "oiScanResults": _frame_records(self.oi_scan_results),
            "oiScanTimestamp": _serialize_value(self.oi_scan_timestamp),
            "oiMag7ScanResults": _frame_records(self.oi_mag7_scan_results),
            "oiMag7ScanTimestamp": _serialize_value(self.oi_mag7_scan_timestamp),
            "oiMag7LastNonEmptyResults": _frame_records(self.oi_mag7_last_non_empty_results),
            "oiMag7LastNonEmptyTimestamp": _serialize_value(self.oi_mag7_last_non_empty_timestamp),
            "oiWatchlistScanResults": _frame_records(self.oi_watchlist_scan_results),
            "oiWatchlistScanTimestamp": _serialize_value(self.oi_watchlist_scan_timestamp),
            "oiWatchlistLastNonEmptyResults": _frame_records(self.oi_watchlist_last_non_empty_results),
            "oiWatchlistLastNonEmptyTimestamp": _serialize_value(self.oi_watchlist_last_non_empty_timestamp),
            "scanResults": _frame_records(self.scan_results),
            "candidateResults": _frame_records(self.candidate_results),
            "scannerHistory": [],
            "scannerHistoryDays": [],
        }

    def _build_dashboard_payload(self) -> dict:
        clock = self.client.get_clock()
        status = self.trader.get_status()
        stream_status = self.client.get_stream_status()
        session_status = self._session_status(clock)
        positions = self.trader.fetch_open_positions_frame()
        trade_history = self._enrich_trade_history(self.repository.get_trade_history(limit=500, profile_id=self.active_profile_id))
        trade_rollups = self.repository.trade_rollups(profile_id=self.active_profile_id)
        option_snapshot = self._sync_all_option_broker_states()
        option_trade_history = self._enrich_option_trade_history(
            self.repository.get_option_trade_history(
                limit=500,
                profile_ids=settings.option_account_profile_ids("paper"),
                broker_only=True,
            )
        )
        option_positions = self._option_positions_frame(option_snapshot.get("positions", []), option_trade_history)
        option_broker_orders = self._option_broker_orders_payload(option_snapshot)
        option_trade_rollups = self.repository.option_trade_rollups(
            profile_ids=settings.option_account_profile_ids("paper"),
            broker_only=True,
        )
        recent_backtests = self.repository.get_recent_backtests()
        recent_catalysts = self._recent_catalysts_or_empty(limit=200)
        latest_catalysts_by_symbol = self._latest_catalysts_or_empty()
        bot_events = self.repository.get_recent_bot_events()
        scanner_history, scanner_history_days = self.repository.get_scanner_history(days=60)
        scanner_history_records = browser_scanner_history_records(_frame_records(scanner_history))
        scanner_history_day_records = _frame_records(scanner_history_days)
        symbol_memory = self.repository.get_symbol_memory()
        learning_status = self._learning_status_payload()
        morning_summary = self._morning_summary(trade_history)

        top_candidate = None
        if not self.candidate_results.empty:
            eligible = self.candidate_results[self.candidate_results["allowed"]]
            source = eligible if not eligible.empty else self.candidate_results
            if "final_score" in source.columns:
                source = source.sort_values(["final_score", "entry"], ascending=[False, False])
            top_candidate = _serialize_value(source.iloc[0].to_dict())
        example_trade = self._example_trade(top_candidate, trade_history)

        backtest_summary_metrics = {
            "totalTrades": int(self.backtest_summary["trades"].sum()) if not self.backtest_summary.empty else 0,
            "winRate": float(self.backtest_summary["win_rate"].mean()) if not self.backtest_summary.empty else 0.0,
            "totalPnL": float(self.backtest_summary["total_pnl"].sum()) if not self.backtest_summary.empty else 0.0,
        }
        option_account_payload = self._option_account_payload()
        option_accounts_payload = self._option_accounts_payload()
        today_key = str(clock.timestamp)[:10]
        stock_book = _history_summary(trade_history, today_key)
        option_book = _history_summary(option_trade_history, today_key)
        option_account_statuses = [account.get("status", {}) for account in option_accounts_payload]
        option_account_status = {
            "accountEquity": sum(float(status.get("accountEquity") or 0) for status in option_account_statuses),
            "tradeableBuyingPower": sum(float(status.get("tradeableBuyingPower") or 0) for status in option_account_statuses),
            "dailyPnL": sum(float(status.get("dailyPnL") or 0) for status in option_account_statuses),
            "openPositions": sum(int(status.get("openPositions") or 0) for status in option_account_statuses),
            "openOrders": sum(int(status.get("openOrders") or 0) for status in option_account_statuses),
        }
        stock_book.update({
            "label": self.client.credentials.label,
            "equity": round(float(status.account_equity or 0), 2),
            "buyingPower": round(float(status.buying_power or 0), 2),
            "dailyPnL": round(float(status.daily_pnl or 0), 2),
            "openPositions": int(status.open_positions or 0),
            "openOrders": int(status.open_orders or 0),
            "botState": self.bot_state,
        })
        option_book.update({
            "label": "Mag7 + Watchlist Options",
            "equity": round(float(option_account_status.get("accountEquity") or 0), 2),
            "buyingPower": round(float(option_account_status.get("tradeableBuyingPower") or 0), 2),
            "dailyPnL": round(float(option_account_status.get("dailyPnL") or 0), 2),
            "openPositions": int(option_account_status.get("openPositions") or len(option_positions)),
            "openOrders": int(option_account_status.get("openOrders") or 0),
            "botState": self.option_bot_state,
        })
        account_books = self._dashboard_account_books(today_key, status, option_accounts_payload)
        live_option_pnl_by_profile: dict[str, float] = {}
        if isinstance(option_positions, pd.DataFrame) and not option_positions.empty:
            for row in option_positions.to_dict("records"):
                profile_id = str(row.get("account_profile_id") or "").strip().lower()
                if not profile_id:
                    continue
                live_option_pnl_by_profile[profile_id] = live_option_pnl_by_profile.get(profile_id, 0.0) + float(
                    row.get("pnl") or row.get("marked_pnl") or row.get("unrealized_pnl") or 0.0
                )
        for book in account_books:
            if book.get("product") == "option":
                book["openPnL"] = round(live_option_pnl_by_profile.get(str(book.get("id") or "").lower(), 0.0), 2)
        dashboard_summary = {
            "asOf": _serialize_value(clock.timestamp),
            "combinedEquity": round(sum(book["equity"] for book in account_books), 2),
            "combinedBuyingPower": round(sum(book["buyingPower"] for book in account_books), 2),
            "combinedDailyPnL": round(sum(book["dailyPnL"] for book in account_books), 2),
            "combinedJournalPnL": round(sum(book["totalPnL"] for book in account_books), 2),
            "totalTrades": sum(book["totalTrades"] for book in account_books),
            "openPositions": sum(book["openPositions"] for book in account_books),
            "openOrders": sum(book["openOrders"] for book in account_books),
            "accountBooks": account_books,
            "stock": stock_book,
            "option": option_book,
            "recentActivity": [
                {"id": row.get("id"), "timestamp": row.get("created_at"), "type": row.get("event_type") or "activity", "message": row.get("message") or "Automation event"}
                for row in bot_events.head(8).to_dict("records")
            ] if isinstance(bot_events, pd.DataFrame) and not bot_events.empty else [],
        }

        return {
            "status": {
                "marketStatus": session_status["currentSession"],
                "sessionStatus": session_status,
                "clockTime": _serialize_value(clock.timestamp),
                "nextOpen": _serialize_value(clock.next_open),
                "nextClose": _serialize_value(clock.next_close),
                "accountEquity": status.account_equity,
                "cash": status.cash,
                "lastEquity": status.last_equity,
                "dailyChange": status.daily_change,
                "dailyChangePct": status.daily_change_pct,
                "buyingPower": status.buying_power,
                "dailyPnL": status.daily_pnl,
                "tradesToday": status.trades_today,
                "openPositions": status.open_positions,
                "openOrders": status.open_orders,
                "accountMode": status.account_mode,
                "accountLabel": self.client.credentials.label,
                "activeAccountId": self.client.credentials.profile_id,
                "scanThreshold": settings.scanner.score_threshold,
                "aiThreshold": settings.ai.min_trade_score,
                "lastRefresh": _serialize_value(self.scan_timestamp),
                "databasePath": str(DATABASE_PATH),
                "watchlistCount": len(settings.scanner.default_universe),
            },
            "accounts": self.available_accounts(),
            "optionAccount": option_account_payload,
            "optionAccounts": option_accounts_payload,
            "dashboardSummary": dashboard_summary,
            "learning": learning_status,
            "runtimeHealth": self._runtime_health_payload(),
            "botState": self.bot_state,
            "optionBot": {
                "state": self.option_bot_state,
                "message": self.option_bot_message,
                "executionMode": self.option_bot_config["approvalMode"],
                "structures": [self.option_bot_config["contractPolicy"]],
                "spreadFilter": self.option_bot_config["spreadFilter"],
                "deltaTarget": self.option_bot_config["deltaTarget"],
                "expectedMove": self.option_bot_config["expectedMove"],
                "requiredOiPriority": "A+ HOT",
                "accountRouting": {
                    "mag7": settings.option_account_profile_id("paper"),
                    "watchlist": settings.watchlist_option_account_profile_id("paper"),
                    "stock": settings.stock_account_profile_id("paper"),
                },
                "accounts": option_accounts_payload,
                "oiConfirmationMaxAgeSeconds": max(
                    int(settings.trading.option_auto_oi_confirmation_max_age_seconds),
                    1,
                ),
                "watchlistSource": self._option_watchlist_source(),
                "watchlistLabel": self._option_watchlist_source_label(),
                "watchlistCount": len(self._option_bot_trade_universe()),
                "tradeUniverseSource": self._option_watchlist_source(),
                "symbolMappings": DEFAULT_OPTION_UNDERLYING_MAP,
                "aliasMap": self._option_alias_map(),
                "account": option_account_payload,
                "agents": self._option_automation_agents(option_positions),
            },
            "optionRiskConfig": self.option_risk_settings,
            "actionMessage": self.action_message,
            "oiActionMessage": self.oi_action_message,
            "scanJob": self.scan_job,
            "oiScanJob": self.oi_scan_job,
            "scannerAuto": {
                "enabled": self.scanner_auto_enabled,
                "engine": "stock-scanner-engine",
                "intervalSeconds": self.scanner_auto_interval_seconds,
                "status": self.scanner_auto_status,
                "message": self.scanner_auto_message,
                "lastRun": _serialize_value(self.scanner_auto_last_run),
                "nextRun": _serialize_value(self.scanner_auto_next_run),
                "lastError": self.scanner_auto_last_error,
            },
            "stockPositionManager": {
                "engine": "stock-position-manager-engine",
                "intervalSeconds": self.stock_position_manager_interval_seconds,
                "status": self.stock_position_manager_status,
                "lastRun": _serialize_value(self.stock_position_manager_last_run),
                "lastError": self.stock_position_manager_last_error,
            },
            "mag7PremarketPlan": self.mag7_premarket_plan_payload(),
            "mag7PremarketChartSignals": self.mag7_chart_signals_payload("premarket"),
            "mag7FiveMinuteChartSignals": self.mag7_chart_signals_payload("regular"),
            "stockAutoReadiness": self._stock_auto_playbook(),
            "oiScannerAuto": {
                "enabled": self.oi_scanner_auto_enabled,
                "scope": "MAG7 watchlist",
                "symbols": self._mag7_oi_underlyings(),
                "optionChainSource": "Schwab/TOS option chain",
                "watchlistEnabled": False,
                "mag7Engine": "mag7-oi-scanner-engine",
                "mag7Mode": "continuous_full_list",
                "watchlistEngine": "disabled",
                "watchlistMode": "disabled",
                "watchlistWorkerCount": 0,
                "watchlistBatchSize": self.oi_watchlist_batch_size,
                "watchlistBatchCount": self.oi_watchlist_batch_count,
                "watchlistBatchesCompleted": self.oi_watchlist_batches_completed,
                "watchlistBatchStart": self.oi_watchlist_batch_start,
                "watchlistBatchEnd": self.oi_watchlist_batch_end,
                "watchlistUniverseCount": self.oi_watchlist_universe_count,
                "watchlistCompletedCycles": self.oi_watchlist_completed_cycles,
                "watchlistCycleStartedAt": _serialize_value(self.oi_watchlist_cycle_started_at),
                "watchlistCycleDurationSeconds": self.oi_watchlist_cycle_duration_seconds,
                "intervalSeconds": self.oi_scanner_auto_interval_seconds,
                "mag7IntervalSeconds": self.oi_mag7_auto_interval_seconds,
                "watchlistIntervalSeconds": None,
                "status": self.oi_scanner_auto_status,
                "message": self.oi_scanner_auto_message,
                "lastRun": _serialize_value(self.oi_scanner_auto_last_run),
                "nextRun": _serialize_value(self.oi_scanner_auto_next_run),
                "lastError": self.oi_scanner_auto_last_error,
            },
            "oiScannerRules": {
                "minPrice": 3.0,
                "minOneHourCloseChangePct": float(settings.scanner.min_one_hour_close_change_pct),
                "minFourHourVolumeChangePct": float(settings.scanner.min_four_hour_volume_change_pct),
                "signalLookbackBars": 2,
                "extendedHours": True,
                "liveCandle": True,
                "liveFiveMinuteEmaVwapRequired": False,
                "minTosRvolAnyTimeframe": float(settings.scanner.tos_rvol_num_dev),
                "minTosRvolMag7AnyTimeframe": float(settings.scanner.tos_rvol_mag7_num_dev),
                "minTosRvolMidTimeframe": float(settings.scanner.tos_rvol_mid_timeframe_num_dev),
                "minTosRvolFiveMinuteEarly": float(settings.scanner.tos_rvol_five_min_early_num_dev),
                "negativeRvolVetoTimeframes": list(self.scanner.TOS_RVOL_NEGATIVE_VETO_TIMEFRAMES),
                "requireMtfGate": False,
                "requireOneHourCloseGate": False,
                "requireFourHourVolumeGate": False,
                "minDelta": 0.20,
                "minExpectedMove": 2.0,
                "maxDaysToExpiration": OI_SCANNER_MAX_DAYS_TO_EXPIRATION,
                "allowZeroDteAfterHours": False,
                "allowZeroDteCore": True,
                "allowedSetups": sorted(OPTION_ALLOWED_SETUPS),
                "runUniverse": "MAG7 watchlist",
                "optionChainSource": "Schwab/TOS option chain",
                "watchlistExcludingMag7Count": 0,
            },
            "stockSignalRules": {
                "minPrice": settings.scanner.min_price,
                "minOneHourCloseChangePct": settings.scanner.min_one_hour_close_change_pct,
                "minOneHourPriceChangePct": settings.scanner.min_one_hour_close_change_pct,
                "minFourHourPriceChangePct": settings.scanner.min_four_hour_price_change_pct,
                "minFourHourVolumeChangePct": settings.scanner.min_four_hour_volume_change_pct,
                "oneHourPriceChangeAutoEntryMode": "required",
                "fourHourPriceChangeAutoEntryMode": "informational_only",
                "fourHourVolumeChangeAutoEntryMode": "required",
                "minTosRvolAnyTimeframe": float(settings.scanner.tos_rvol_num_dev),
                "minTosRvolMag7AnyTimeframe": float(settings.scanner.tos_rvol_mag7_num_dev),
                "minTosRvolMidTimeframe": float(settings.scanner.tos_rvol_mid_timeframe_num_dev),
                "minTosRvolFiveMinuteEarly": float(settings.scanner.tos_rvol_five_min_early_num_dev),
                "negativeRvolVetoTimeframes": list(self.scanner.TOS_RVOL_NEGATIVE_VETO_TIMEFRAMES),
                "lookbackBars": settings.scanner.signal_lookback_bars,
                "regularSessionOnly": False,
                "livePriceChangeUsesCurrentCandle": True,
                "liveFourHourVolumeUsesCurrentCandle": True,
                "signalIncludeExtendedHours": settings.scanner.signal_include_extended_hours,
                "ema9RetestLookbackBars": settings.scanner.ema9_retest_lookback_bars,
            },
            "llmAdvisor": {
                "enabled": settings.ai.llm_agent_enabled,
                "externalEnabled": settings.ai.llm_agent_external_enabled,
                "provider": settings.ai.llm_agent_provider,
                "model": settings.ai.llm_agent_model,
                "mode": "non_blocking_advisory_only",
                "canBlockTrades": False,
                "maxCandidates": settings.ai.llm_agent_max_candidates,
            },
            "watchlist": settings.scanner.default_universe,
            "optionWatchlist": self.option_watchlist,
            "activeOptionWatchlist": self._active_option_watchlist(),
            "mag7OptionWatchlist": self._mag7_option_underlyings(),
            "mag7OptionWatchlistSource": self.mag7_scanner_watchlist,
            "scanResults": _frame_records(self.scan_results),
            "candidateResults": _frame_records(self.candidate_results),
            "mag7ScanResults": _frame_records(self.mag7_scan_results),
            "mag7CandidateResults": _frame_records(self.mag7_candidate_results),
            "mag7ScanTimestamp": _serialize_value(self.mag7_scan_timestamp),
            "oiScanResults": _frame_records(self.oi_scan_results),
            "oiScanTimestamp": _serialize_value(self.oi_scan_timestamp),
            "oiMag7ScanResults": _frame_records(self.oi_mag7_scan_results),
            "oiMag7ScanTimestamp": _serialize_value(self.oi_mag7_scan_timestamp),
            "oiMag7LastNonEmptyResults": _frame_records(self.oi_mag7_last_non_empty_results),
            "oiMag7LastNonEmptyTimestamp": _serialize_value(self.oi_mag7_last_non_empty_timestamp),
            "oiWatchlistScanResults": _frame_records(self.oi_watchlist_scan_results),
            "oiWatchlistScanTimestamp": _serialize_value(self.oi_watchlist_scan_timestamp),
            "oiWatchlistLastNonEmptyResults": _frame_records(self.oi_watchlist_last_non_empty_results),
            "oiWatchlistLastNonEmptyTimestamp": _serialize_value(self.oi_watchlist_last_non_empty_timestamp),
            "scannerHistory": scanner_history_records,
            "scannerHistoryDays": scanner_history_day_records,
            "scannerHistoryVersion": scanner_history_version(
                scanner_history_records, scanner_history_day_records,
            ),
            "optionCandidateResults": _frame_records(self.option_candidate_results),
            "optionPlanBlocks": _serialize_value(self.option_plan_blocks),
            "optionScanCoverage": _serialize_value(self._option_scan_coverage_payload()),
            "optionNoCandidateResults": _serialize_value(self._option_no_candidate_results()),
            "optionSupervisorReport": _serialize_value(getattr(self, "option_supervisor_report", None) or self._empty_option_supervisor_report()),
            "topCandidate": top_candidate,
            "exampleTrade": example_trade,
            "openPositions": _frame_records(positions),
            "tradeHistory": _frame_records(trade_history),
            "optionTradeHistory": _frame_records(option_trade_history),
            "optionPositions": _frame_records(option_positions),
            "optionBrokerOrders": _serialize_value(option_broker_orders),
            "journalRollups": {
                "daily": _frame_records(trade_rollups.get("daily", pd.DataFrame())),
                "weekly": _frame_records(trade_rollups.get("weekly", pd.DataFrame())),
                "monthly": _frame_records(trade_rollups.get("monthly", pd.DataFrame())),
            },
            "optionJournalRollups": {
                "daily": _frame_records(option_trade_rollups.get("daily", pd.DataFrame())),
                "weekly": _frame_records(option_trade_rollups.get("weekly", pd.DataFrame())),
                "monthly": _frame_records(option_trade_rollups.get("monthly", pd.DataFrame())),
            },
            "backtest": backtest_summary_metrics,
            "backtestSummary": _frame_records(self.backtest_summary),
            "backtestTrades": _frame_records(self.backtest_trades),
            "backtestJob": self.backtest_job,
            "morningSummary": morning_summary,
            "recentBacktests": _frame_records(recent_backtests),
            "agentDecisions": self._agent_decisions(clock, status, session_status),
            "catalysts": _frame_records(recent_catalysts),
            "catalystIndex": _frame_records(latest_catalysts_by_symbol),
            "botEvents": _frame_records(bot_events),
            "symbolMemory": _frame_records(symbol_memory),
            "portfolio": self._portfolio_state(status),
            "riskConfig": self._risk_settings_payload(),
            "scannerStorageConfig": self._scanner_storage_payload(),
            "scheduler": {
                "enabled": self.scheduler_enabled,
                "intervalSeconds": self.scheduler_interval_seconds,
                "lastRun": _serialize_value(self.scheduler_last_run),
                "nextRun": _serialize_value(self.scheduler_next_run),
                "cycleCount": self.scheduler_cycle_count,
                "status": self.scheduler_cycle_status,
                "message": self.scheduler_cycle_message,
                "lastError": self.scheduler_last_error,
                "entryBlockStatus": self.entry_block_status,
                "entryBlockMessage": self.entry_block_message,
            },
            "streaming": {
                "marketDataProvider": settings.market_data_provider,
                "signalSource": "Schwab/TOS API" if settings.market_data_provider == "schwab" else "Alpaca Market Data",
                "executionBroker": "Alpaca Paper",
                "enabled": stream_status.enabled,
                "marketDataConnected": stream_status.marketDataConnected,
                "tradeUpdatesConnected": stream_status.tradeUpdatesConnected,
                "subscribedSymbols": stream_status.subscribedSymbols,
                "lastBarAt": _serialize_value(stream_status.lastBarAt),
                "lastQuoteAt": _serialize_value(stream_status.lastQuoteAt),
                "lastTradeUpdateAt": _serialize_value(stream_status.lastTradeUpdateAt),
                "startedAt": _serialize_value(stream_status.startedAt),
                "lastError": stream_status.lastError,
            },
        }

    def _refresh_dashboard_cache(self) -> None:
        try:
            payload = self._build_dashboard_payload()
            with self.dashboard_cache_lock:
                self.dashboard_cache = payload
                self.dashboard_cache_timestamp = datetime.now().astimezone()
        except Exception as exc:
            self.repository.log_bot_event("dashboard_cache_error", str(exc))
        finally:
            with self.dashboard_cache_lock:
                self.dashboard_refresh_thread = None

    def dashboard_payload(self) -> dict:
        now = datetime.now().astimezone()
        stale_cached = None
        with self.dashboard_cache_lock:
            cached = self.dashboard_cache
            cached_at = self.dashboard_cache_timestamp
            refresh_running = self.dashboard_refresh_thread is not None and self.dashboard_refresh_thread.is_alive()
            cache_fresh = (
                cached is not None
                and cached_at is not None
                and (now - cached_at).total_seconds() <= DASHBOARD_FULL_CACHE_TTL_SECONDS
            )
            if cache_fresh:
                return cached
            if cached is not None:
                if not refresh_running:
                    self.dashboard_refresh_thread = threading.Thread(target=self._refresh_dashboard_cache, daemon=True)
                    self.dashboard_refresh_thread.start()
                stale_cached = cached
        if stale_cached is not None:
            dynamic = self._dashboard_payload_minimal()
            merged = dict(stale_cached)
            for key in (
                "botState",
                "optionBot",
                "learning",
                "actionMessage",
                "oiActionMessage",
                "scanJob",
                "oiScanJob",
                "runtimeHealth",
                "scannerAuto",
                "stockPositionManager",
                "scheduler",
                "premarketPlan",
                "mag7PremarketPlan",
                "oiScannerAuto",
                "scanResults",
                "candidateResults",
                "oiScanResults",
                "oiScanTimestamp",
                "oiMag7ScanResults",
                "oiMag7ScanTimestamp",
                "oiWatchlistScanResults",
                "oiWatchlistScanTimestamp",
            ):
                if key in dynamic:
                    merged[key] = dynamic[key]
            return merged
        if refresh_running:
            return self._dashboard_payload_minimal()
        payload = self._dashboard_payload_minimal()
        with self.dashboard_cache_lock:
            self.dashboard_cache = payload
            self.dashboard_cache_timestamp = datetime.now().astimezone() - timedelta(seconds=10)
            if self.dashboard_refresh_thread is None or not self.dashboard_refresh_thread.is_alive():
                self.dashboard_refresh_thread = threading.Thread(target=self._refresh_dashboard_cache, daemon=True)
                self.dashboard_refresh_thread.start()
        return payload

    def _enrich_trade_history(self, trade_history: pd.DataFrame) -> pd.DataFrame:
        if trade_history is None or trade_history.empty:
            return pd.DataFrame()

        frame = trade_history.copy()
        for column in ["entry_price", "stop_price", "target_price", "quantity", "pnl", "score"]:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

        if "entry_price" in frame.columns and "stop_price" in frame.columns:
            frame["stop_loss_pct"] = (
                ((frame["entry_price"] - frame["stop_price"]) / frame["entry_price"].replace(0, pd.NA)) * 100
            ).round(2)
        else:
            frame["stop_loss_pct"] = None

        if "pnl" in frame.columns and "entry_price" in frame.columns and "quantity" in frame.columns:
            base_notional = (frame["entry_price"] * frame["quantity"]).replace(0, pd.NA)
            frame["profit_pct"] = ((frame["pnl"] / base_notional) * 100).round(2)
        else:
            frame["profit_pct"] = None

        frame["ai_score"] = frame["score"].round(2) if "score" in frame.columns else None
        frame["ai_confidence"] = None
        frame["ai_model_name"] = None
        frame["model_win_probability"] = None
        frame["model_expected_r"] = None
        frame["llm_advice"] = None
        frame["llm_summary"] = None
        frame["llm_strengths"] = None
        frame["llm_cautions"] = None
        frame["llm_tags"] = None
        frame["llm_confidence"] = None
        frame["llm_rank_score"] = None
        frame["llm_agent_mode"] = None
        frame["llm_agent_model"] = None
        frame["llm_agent_non_blocking"] = True
        frame["partial_exit_taken"] = False
        frame["partial_exit_price"] = None
        frame["partial_exit_qty"] = None
        frame["runner_stop"] = frame["stop_price"] if "stop_price" in frame.columns else None
        frame["runner_stop_locked_pct"] = None
        frame["runner_exit_price"] = None
        frame["runner_exit_reason"] = None
        if "analysis_json" in frame.columns:
            analysis_series = frame["analysis_json"].fillna("")
            parsed = analysis_series.apply(self._safe_json_loads)
            frame["ai_confidence"] = parsed.apply(lambda item: item.get("ai_confidence") if isinstance(item, dict) else None)
            frame["ai_model_name"] = parsed.apply(lambda item: item.get("ai_model_name") if isinstance(item, dict) else None)
            frame["model_win_probability"] = parsed.apply(lambda item: item.get("model_win_probability") if isinstance(item, dict) else None)
            frame["model_expected_r"] = parsed.apply(lambda item: item.get("model_expected_r") if isinstance(item, dict) else None)
            frame["llm_advice"] = parsed.apply(lambda item: item.get("llm_advice") if isinstance(item, dict) else None)
            frame["llm_summary"] = parsed.apply(lambda item: item.get("llm_summary") if isinstance(item, dict) else None)
            frame["llm_strengths"] = parsed.apply(lambda item: item.get("llm_strengths") if isinstance(item, dict) else None)
            frame["llm_cautions"] = parsed.apply(lambda item: item.get("llm_cautions") if isinstance(item, dict) else None)
            frame["llm_tags"] = parsed.apply(lambda item: item.get("llm_tags") if isinstance(item, dict) else None)
            frame["llm_confidence"] = parsed.apply(lambda item: item.get("llm_confidence") if isinstance(item, dict) else None)
            frame["llm_rank_score"] = parsed.apply(lambda item: item.get("llm_rank_score") if isinstance(item, dict) else None)
            frame["llm_agent_mode"] = parsed.apply(lambda item: item.get("llm_agent_mode") if isinstance(item, dict) else None)
            frame["llm_agent_model"] = parsed.apply(lambda item: item.get("llm_agent_model") if isinstance(item, dict) else None)
            frame["llm_agent_non_blocking"] = parsed.apply(lambda item: bool(item.get("llm_agent_non_blocking", True)) if isinstance(item, dict) else True)
            frame["partial_exit_taken"] = parsed.apply(lambda item: bool(item.get("partial_exit_taken")) if isinstance(item, dict) else False)
            frame["partial_exit_price"] = parsed.apply(lambda item: item.get("partial_exit_price") if isinstance(item, dict) else None)
            frame["partial_exit_qty"] = parsed.apply(lambda item: item.get("partial_exit_qty") if isinstance(item, dict) else None)
            frame["runner_stop"] = parsed.apply(lambda item: item.get("runner_stop") if isinstance(item, dict) and item.get("runner_stop") is not None else None)
            frame["runner_exit_price"] = parsed.apply(lambda item: item.get("runner_exit_price") if isinstance(item, dict) else None)
            frame["runner_exit_reason"] = parsed.apply(lambda item: item.get("runner_exit_reason") if isinstance(item, dict) else None)
            frame["runner_stop_locked_pct"] = parsed.apply(lambda item: item.get("runner_stop_locked_pct") if isinstance(item, dict) else None)
        if "runner_stop_locked_pct" in frame.columns:
            fallback_locked_pct = (
                ((frame["runner_stop"].fillna(frame["stop_price"]) - frame["entry_price"]) / frame["entry_price"].replace(0, pd.NA)) * 100
            ).round(2)
            frame["runner_stop_locked_pct"] = pd.to_numeric(frame["runner_stop_locked_pct"], errors="coerce").fillna(fallback_locked_pct)
        frame["entry_time"] = frame.get("opened_at")
        frame["exit_time"] = frame.get("closed_at")
        frame["exit_reason"] = frame.get("notes")
        frame["account_profile_id"] = frame.get("account_profile_id")
        frame["account_label"] = frame.get("account_label")
        return frame

    def _enrich_option_trade_history(self, trade_history: pd.DataFrame) -> pd.DataFrame:
        if trade_history is None or trade_history.empty:
            return pd.DataFrame()

        frame = trade_history.copy()
        for column in ["quantity", "entry_price", "exit_price", "stop_price", "target_price", "max_loss_amount", "pnl"]:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

        base_notional = (frame["entry_price"] * frame["quantity"]).replace(0, pd.NA) if {"entry_price", "quantity"}.issubset(frame.columns) else pd.Series(dtype="float64")
        if not base_notional.empty:
            frame["profit_pct"] = ((frame["pnl"] / base_notional) * 100).round(2)
        else:
            frame["profit_pct"] = None

        frame["entry_time"] = frame.get("opened_at")
        frame["exit_time"] = frame.get("closed_at")
        frame["contract"] = frame.get("option_symbol")
        frame["symbol"] = frame.get("underlying_symbol")
        frame["selected_option_symbol"] = frame.get("option_symbol")
        frame["selected_option_bid"] = None
        frame["selected_option_ask"] = None
        frame["selected_option_mid"] = frame.get("entry_price")
        frame["selected_option_delta"] = None
        frame["selected_option_expected_move"] = None
        frame["selected_option_expiry"] = None
        frame["selected_option_strike"] = None
        frame["selected_option_volume"] = None
        frame["selected_option_open_interest"] = None
        frame["underlying_target_1_strike"] = None
        frame["underlying_target_1_sell_percent"] = None
        frame["liquidity_breakout_required"] = None
        frame["liquidity_atm_dominates_otm"] = None
        frame["underlying_target_liquidity_metric"] = None
        frame["option_rule_trigger_match"] = None
        frame["option_one_hour_close_change_pct"] = None
        frame["option_one_hour_price_change_pct"] = None
        frame["option_four_hour_close_change_pct"] = None
        frame["option_four_hour_price_change_pct"] = None
        frame["option_four_hour_volume_change_pct"] = None
        frame["option_live_four_hour_volume_change_pct"] = None
        frame["option_live_four_hour_current_volume"] = None
        frame["option_live_four_hour_volume_2_bars_ago"] = None
        frame["option_rule_passed"] = None
        frame["current_mid"] = None
        frame["take_profit_1"] = frame.get("target_price")
        frame["contracts_to_sell_at_target_1"] = None
        frame["remaining_quantity"] = frame.get("quantity")
        frame["realized_pnl"] = 0.0
        frame["unrealized_pnl"] = 0.0
        frame["marked_pnl"] = frame.get("pnl")
        frame["partial_exit_taken"] = False
        frame["partial_exit_price"] = None
        frame["partial_exit_qty"] = None
        frame["runner_stop"] = frame.get("stop_price")
        frame["runner_stop_locked_pct"] = None
        frame["runner_exit_price"] = None
        frame["runner_exit_reason"] = None
        frame["stop_loss_percent"] = None
        frame["stop_loss_mode"] = None
        frame["stop_loss_label"] = None
        frame["first_profit_target_percent"] = None
        frame["broker_submit_ms"] = None
        frame["fast_execution_path"] = None
        if "analysis_json" in frame.columns:
            parsed = frame["analysis_json"].fillna("").apply(self._safe_json_loads)
            frame["selected_option_symbol"] = parsed.apply(
                lambda item: item.get("selected_option_symbol") if isinstance(item, dict) and item.get("selected_option_symbol") else None
            ).fillna(frame["option_symbol"])
            frame["selected_option_bid"] = parsed.apply(lambda item: item.get("selected_option_bid") if isinstance(item, dict) else None)
            frame["selected_option_ask"] = parsed.apply(lambda item: item.get("selected_option_ask") if isinstance(item, dict) else None)
            parsed_mid = parsed.apply(lambda item: item.get("selected_option_mid") if isinstance(item, dict) else None)
            frame["selected_option_mid"] = pd.to_numeric(parsed_mid, errors="coerce").fillna(pd.to_numeric(frame["entry_price"], errors="coerce"))
            frame["selected_option_delta"] = parsed.apply(lambda item: item.get("selected_option_delta") if isinstance(item, dict) else None)
            frame["selected_option_expected_move"] = parsed.apply(lambda item: item.get("selected_option_expected_move") if isinstance(item, dict) else None)
            frame["selected_option_expiry"] = parsed.apply(lambda item: item.get("selected_option_expiry") if isinstance(item, dict) else None)
            frame["selected_option_strike"] = parsed.apply(lambda item: item.get("selected_option_strike") if isinstance(item, dict) else None)
            frame["selected_option_volume"] = parsed.apply(lambda item: item.get("selected_option_volume") if isinstance(item, dict) else None)
            frame["selected_option_open_interest"] = parsed.apply(lambda item: item.get("selected_option_open_interest") if isinstance(item, dict) else None)
            frame["underlying_target_1_strike"] = parsed.apply(lambda item: item.get("underlying_target_1_strike") if isinstance(item, dict) else None)
            frame["underlying_target_1_sell_percent"] = parsed.apply(lambda item: item.get("underlying_target_1_sell_percent") if isinstance(item, dict) else None)
            frame["liquidity_breakout_required"] = parsed.apply(lambda item: item.get("liquidity_breakout_required") if isinstance(item, dict) else None)
            frame["liquidity_atm_dominates_otm"] = parsed.apply(lambda item: item.get("liquidity_atm_dominates_otm") if isinstance(item, dict) else None)
            frame["underlying_target_liquidity_metric"] = parsed.apply(lambda item: item.get("underlying_target_liquidity_metric") if isinstance(item, dict) else None)
            frame["option_rule_trigger_match"] = parsed.apply(lambda item: item.get("option_rule_trigger_match") if isinstance(item, dict) else None)
            frame["option_one_hour_close_change_pct"] = parsed.apply(lambda item: item.get("option_one_hour_close_change_pct") if isinstance(item, dict) else None)
            frame["option_one_hour_price_change_pct"] = parsed.apply(
                lambda item: item.get("option_one_hour_price_change_pct", item.get("option_one_hour_close_change_pct"))
                if isinstance(item, dict) else None
            )
            frame["option_four_hour_close_change_pct"] = parsed.apply(lambda item: item.get("option_four_hour_close_change_pct") if isinstance(item, dict) else None)
            frame["option_four_hour_price_change_pct"] = parsed.apply(
                lambda item: item.get("option_four_hour_price_change_pct", item.get("option_four_hour_close_change_pct"))
                if isinstance(item, dict) else None
            )
            frame["option_four_hour_volume_change_pct"] = parsed.apply(lambda item: item.get("option_four_hour_volume_change_pct") if isinstance(item, dict) else None)
            frame["option_live_four_hour_volume_change_pct"] = parsed.apply(lambda item: item.get("option_live_four_hour_volume_change_pct") if isinstance(item, dict) else None)
            frame["option_live_four_hour_current_volume"] = parsed.apply(lambda item: item.get("option_live_four_hour_current_volume") if isinstance(item, dict) else None)
            frame["option_live_four_hour_volume_2_bars_ago"] = parsed.apply(lambda item: item.get("option_live_four_hour_volume_2_bars_ago") if isinstance(item, dict) else None)
            frame["option_rule_passed"] = parsed.apply(lambda item: item.get("option_rule_passed") if isinstance(item, dict) else None)
            frame["current_mid"] = parsed.apply(lambda item: item.get("current_mid") if isinstance(item, dict) else None)
            frame["broker_submit_ms"] = parsed.apply(lambda item: item.get("broker_submit_ms") if isinstance(item, dict) else None)
            frame["fast_execution_path"] = parsed.apply(lambda item: item.get("fast_execution_path") if isinstance(item, dict) else None)
            frame["take_profit_1"] = parsed.apply(lambda item: item.get("take_profit_1") if isinstance(item, dict) else None).fillna(frame.get("target_price"))
            frame["contracts_to_sell_at_target_1"] = parsed.apply(lambda item: item.get("contracts_to_sell_at_target_1") if isinstance(item, dict) else None)
            frame["remaining_quantity"] = parsed.apply(lambda item: item.get("remaining_quantity") if isinstance(item, dict) else None).fillna(frame.get("quantity"))
            frame["realized_pnl"] = parsed.apply(lambda item: item.get("realized_pnl") if isinstance(item, dict) else None)
            frame["unrealized_pnl"] = parsed.apply(lambda item: item.get("unrealized_pnl") if isinstance(item, dict) else None)
            frame["marked_pnl"] = parsed.apply(lambda item: item.get("marked_pnl") if isinstance(item, dict) else None).fillna(frame.get("pnl"))
            frame["partial_exit_taken"] = parsed.apply(lambda item: bool(item.get("partial_exit_taken")) if isinstance(item, dict) else False)
            frame["partial_exit_price"] = parsed.apply(lambda item: item.get("partial_exit_price") if isinstance(item, dict) else None)
            frame["partial_exit_qty"] = parsed.apply(lambda item: item.get("partial_exit_qty") if isinstance(item, dict) else None)
            frame["runner_stop"] = parsed.apply(lambda item: item.get("runner_stop") if isinstance(item, dict) and item.get("runner_stop") is not None else None).fillna(frame.get("stop_price"))
            frame["runner_stop_locked_pct"] = parsed.apply(lambda item: item.get("runner_stop_locked_pct") if isinstance(item, dict) else None)
            frame["runner_exit_price"] = parsed.apply(lambda item: item.get("runner_exit_price") if isinstance(item, dict) else None)
            frame["runner_exit_reason"] = parsed.apply(lambda item: item.get("runner_exit_reason") if isinstance(item, dict) else None)
            frame["stop_loss_percent"] = parsed.apply(lambda item: item.get("stop_loss_percent") if isinstance(item, dict) else None)
            frame["stop_loss_mode"] = parsed.apply(lambda item: item.get("stop_loss_mode") if isinstance(item, dict) else None)
            frame["stop_loss_label"] = parsed.apply(lambda item: item.get("stop_loss_label") if isinstance(item, dict) else None)
            frame["first_profit_target_percent"] = parsed.apply(lambda item: item.get("first_profit_target_percent") if isinstance(item, dict) else None)
        return frame

    def _safe_json_loads(self, raw: str):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _example_trade(self, top_candidate: dict | None, trade_history: pd.DataFrame) -> dict:
        if top_candidate:
            ai_score = int(top_candidate.get("final_score", top_candidate.get("score", 0)))
            return {
                "source": "scanner_candidate",
                "symbol": top_candidate.get("symbol", "AAPL"),
                "strategyFamily": top_candidate.get("strategy_family", "Momentum + Price Action Trend"),
                "setupName": top_candidate.get("setup_name", "EMA + VWAP + ORB"),
                "policyName": top_candidate.get("policy_name", "Signal Selector -> Policy Critic -> Risk Governor -> Execution Router"),
                "policyStatus": top_candidate.get("policy_status", "Approved"),
                "executionRoute": top_candidate.get("execution_route", "Bracket Order / Core Session"),
                "entry": top_candidate.get("entry"),
                "stopLoss": top_candidate.get("stop_loss"),
                "target": top_candidate.get("target"),
                "riskPerShare": top_candidate.get("risk_per_share"),
                "aiScore": ai_score,
                "triggerSource": top_candidate.get("trigger_source"),
                "blueprint": top_candidate.get("trade_blueprint"),
                "rationale": top_candidate.get("rationale"),
                "llmAdvice": top_candidate.get("llm_advice"),
                "llmSummary": top_candidate.get("llm_summary"),
                "llmTags": top_candidate.get("llm_tags"),
            }

        if trade_history is not None and not trade_history.empty:
            row = trade_history.iloc[0].to_dict()
            return {
                "source": "journal_trade",
                "symbol": row.get("symbol", "AAPL"),
                "strategyFamily": row.get("strategy_family", "Momentum + Price Action Trend"),
                "setupName": row.get("setup_name", "EMA + VWAP + ORB"),
                "policyName": "Signal Selector -> Policy Critic -> Risk Governor -> Execution Router",
                "policyStatus": row.get("policy_status", row.get("status", "Logged")),
                "executionRoute": row.get("execution_route", row.get("session_name", "Paper Route")),
                "entry": row.get("entry_price"),
                "stopLoss": row.get("stop_price"),
                "target": row.get("target_price"),
                "riskPerShare": (
                    round(float(row.get("entry_price", 0)) - float(row.get("stop_price", 0)), 2)
                    if row.get("entry_price") is not None and row.get("stop_price") is not None
                    else None
                ),
                "aiScore": row.get("score"),
                "triggerSource": row.get("trigger_source"),
                "blueprint": row.get("trade_blueprint"),
                "rationale": row.get("entry_reason") or row.get("notes"),
                "llmAdvice": row.get("llm_advice"),
                "llmSummary": row.get("llm_summary"),
                "llmTags": row.get("llm_tags"),
            }

        return {
            "source": "template",
            "symbol": "AAPL",
            "strategyFamily": "Momentum + Price Action Trend",
            "setupName": "EMA + VWAP + ORB",
            "policyName": "Signal Selector -> Policy Critic -> Risk Governor -> Execution Router",
            "policyStatus": "Awaiting Scan",
            "executionRoute": "Bracket Order / Core Session",
            "entry": 185.2,
            "stopLoss": 182.9,
            "target": 189.8,
            "riskPerShare": 2.3,
            "aiScore": 91,
            "triggerSource": "opening_range_high",
            "blueprint": "Setup EMA + VWAP + ORB -> Policy Approved -> Entry 185.20 / Stop 182.90 / Target 189.80 -> Route Bracket Order / Core Session",
            "rationale": "Example only. Run scan or backtest to replace with live system output.",
            "llmAdvice": "Example only",
            "llmSummary": "LLM advisor is informational only and cannot block execution.",
            "llmTags": ["non_blocking"],
        }

    def _morning_summary(self, trade_history: pd.DataFrame) -> dict:
        taken = []
        if trade_history is not None and not trade_history.empty:
            recent_trades = trade_history.head(8).to_dict("records")
            for row in recent_trades:
                taken.append(
                    {
                        "symbol": row.get("symbol"),
                        "setup": row.get("setup_name"),
                        "route": row.get("execution_route") or row.get("session_name"),
                        "status": row.get("status"),
                        "entry": row.get("entry_price"),
                        "openedAt": row.get("opened_at"),
                        "pnl": row.get("pnl"),
                    }
                )

        rejected = []
        if self.candidate_results is not None and not self.candidate_results.empty:
            blocked = self.candidate_results[self.candidate_results["allowed"] == False].head(8)  # noqa: E712
            for row in blocked.to_dict("records"):
                rejected.append(
                    {
                        "symbol": row.get("symbol"),
                        "setup": row.get("setup_name"),
                        "reason": row.get("rejection_reason"),
                        "score": row.get("final_score", row.get("score")),
                    }
                )

        next_actions = []
        if taken:
            next_actions.append("Paper trades were submitted and logged to the journal.")
        elif rejected:
            next_actions.append("No trades were taken because all current candidates failed the final approval gate.")
        else:
            next_actions.append("No completed overnight trade decisions yet.")

        if self.bot_state == "Running":
            next_actions.append("Agent Alpha is still running and will continue scanning automatically.")
        if self.client.get_clock().is_open:
            next_actions.append("Core market is open, so bracket paper orders can be used.")
        else:
            next_actions.append("Extended-hours routing is active until core market opens.")

        return {
            "overnightTrades": len(taken),
            "rejectedSetups": len(rejected),
            "taken": taken,
            "rejected": rejected,
            "nextActions": next_actions,
        }

    def _session_status(self, clock) -> dict:
        now = clock.timestamp.astimezone(self.backtester._tz) if clock.timestamp else datetime.now(tz=self.backtester._tz)
        current_time = now.time().replace(tzinfo=None)
        weekday = now.weekday()

        premarket_open = weekday < 5 and clock_time(4, 0) <= current_time < clock_time(9, 30)
        afterhours_open = weekday < 5 and clock_time(16, 0) <= current_time < clock_time(20, 0)

        if weekday == 6:
            overnight_open = current_time >= clock_time(20, 0)
        elif weekday in {0, 1, 2, 3}:
            overnight_open = current_time < clock_time(4, 0) or current_time >= clock_time(20, 0)
        elif weekday == 4:
            overnight_open = current_time < clock_time(4, 0)
        else:
            overnight_open = False

        return {
            "core": "Open" if clock.is_open else "Closed",
            "extended": "Open" if premarket_open or afterhours_open else "Closed",
            "overnight": "Open" if overnight_open else "Closed",
            "crypto": "Open",
            "currentSession": self._current_session_name(
                clock.is_open,
                premarket_open,
                afterhours_open,
                overnight_open,
            ),
            "canAutoTrade": bool(clock.is_open or premarket_open or afterhours_open or overnight_open),
            "automationMode": "Auto Trade" if (clock.is_open or premarket_open or afterhours_open or overnight_open) else "Waiting",
            "executionNote": (
                "Regular market is open. Scanner can run, core entries use market orders, and core exits use market execution."
                if clock.is_open
                else "Extended-hours stock session detected. Bot can scan and use marketable extended-hours limit entries and exits."
                if premarket_open or afterhours_open or overnight_open
                else "US stock sessions are closed. Scheduler can remain on and resume automatically at the next tradable session."
            ),
        }

    def _current_session_name(
        self,
        core_open: bool,
        premarket_open: bool,
        afterhours_open: bool,
        overnight_open: bool,
    ) -> str:
        if core_open:
            return "Core"
        if premarket_open:
            return "Pre-Market"
        if afterhours_open:
            return "After-Hours"
        if overnight_open:
            return "Overnight"
        return "Closed"

    def _portfolio_state(self, status) -> dict:
        equity = float(status.account_equity)
        max_daily_loss = equity * settings.trading.max_daily_loss_pct if settings.trading.enforce_daily_loss_limit else 0.0
        trade_capital = float(settings.trading.fixed_trade_amount)
        risk_per_trade = trade_capital * (settings.trading.stop_loss_percent / 100)
        remaining_daily_loss = max_daily_loss + float(status.daily_pnl) if settings.trading.enforce_daily_loss_limit else None
        remaining_trades = "Unlimited" if settings.trading.max_trades_per_day <= 0 else max(settings.trading.max_trades_per_day - int(status.trades_today), 0)
        daily_trade_amount = float(settings.trading.daily_trade_amount)
        deployed_capital = float(getattr(status, "deployed_capital", 0.0))
        remaining_capital = max(float(status.buying_power), 0.0)
        remaining_trade_slots = (
            "Unlimited"
            if trade_capital <= 0
            else max(int(remaining_capital // trade_capital), 0)
        )
        return {
            "equity": equity,
            "cash": float(status.cash),
            "lastEquity": float(status.last_equity),
            "dailyChange": float(status.daily_change),
            "dailyChangePct": float(status.daily_change_pct),
            "buyingPower": float(status.buying_power),
            "dailyTradeAmount": round(daily_trade_amount, 2),
            "deployedCapital": round(deployed_capital, 2),
            "remainingCapital": round(remaining_capital, 2),
            "remainingTradeSlotsByCapital": remaining_trade_slots,
            "riskPerTrade": round(risk_per_trade, 2),
            "maxDailyLoss": round(max_daily_loss, 2),
            "remainingDailyLoss": round(remaining_daily_loss, 2) if remaining_daily_loss is not None else None,
            "dailyLossLimitEnabled": bool(settings.trading.enforce_daily_loss_limit),
            "remainingTrades": remaining_trades,
            "rewardToRisk": settings.trading.reward_to_risk,
            "paperOnly": self.client.is_paper,
        }

    def _risk_settings_payload(self) -> dict:
        return {
            "dailyTradeAmount": round(float(settings.trading.daily_trade_amount), 2),
            "tradeAmount": round(float(settings.trading.fixed_trade_amount), 2),
            "stopLossPercent": round(float(settings.trading.stop_loss_percent), 2),
            "stopLossAmount": round(float(settings.trading.stop_loss_amount), 2),
            "firstProfitTargetPercent": round(float(settings.trading.take_profit_1_pct), 2),
            "rewardToRisk": round(float(settings.trading.reward_to_risk), 2),
            "dailyLossLimitEnabled": bool(settings.trading.enforce_daily_loss_limit),
        }

    def _scanner_storage_payload(self) -> dict:
        return {
            "scannerHistoryRetentionDays": int(settings.scanner.history_retention_days),
        }

    def _agent_decisions(self, clock, status, session_status) -> list[dict]:
        eligible_count = 0
        rejected_count = 0
        if not self.candidate_results.empty and "allowed" in self.candidate_results.columns:
            eligible_count = int(self.candidate_results["allowed"].sum())
            rejected_count = int((~self.candidate_results["allowed"]).sum())

        scan_count = len(self.scan_results)
        daily_loss_limit = status.account_equity * settings.trading.max_daily_loss_pct
        loss_buffer = daily_loss_limit + status.daily_pnl
        daily_loss_clear = (not settings.trading.enforce_daily_loss_limit) or loss_buffer > 0
        risk_ok = daily_loss_clear and (
            settings.trading.max_trades_per_day <= 0
            or status.trades_today < settings.trading.max_trades_per_day
        )

        return [
            {
                "agent": "Market Regime",
                "status": session_status["currentSession"],
                "confidence": 78 if session_status["core"] == "Open" else 62 if session_status["extended"] == "Open" or session_status["overnight"] == "Open" else 45,
                "summary": session_status["executionNote"],
            },
            {
                "agent": "Signal Selector",
                "status": "Momentum Price Action Trend",
                "confidence": 84,
                "summary": "Active playbook: EMA + VWAP + ORB, previous day high, premarket high, premarket low above candle, previous day low above candle, and EMA + VWAP trend continuation.",
            },
            {
                "agent": "Policy Critic",
                "status": f"{eligible_count} approved / {rejected_count} rejected",
                "confidence": min(95, 50 + (scan_count * 5)),
                "summary": f"{scan_count} scanner rows were checked against deterministic rule gates, liquidity quality, and session policy. AI scores are advisory-only.",
            },
            {
                "agent": "AI Scoring",
                "status": (
                    f"Top score {int(self.candidate_results['final_score'].max())}"
                    if not self.candidate_results.empty and "final_score" in self.candidate_results.columns
                    else "No score yet"
                ),
                "confidence": (
                    int(self.candidate_results["final_score"].max())
                    if not self.candidate_results.empty and "final_score" in self.candidate_results.columns
                    else 45
                ),
                "summary": (
                    f"Hard rule gate {settings.trading.min_rule_score}; AI/model scores are advisory-only."
                ),
            },
            {
                "agent": "LLM Advisor",
                "status": "Non-blocking",
                "confidence": 90,
                "summary": (
                    "Reviews/ranks/tags trade context for journal and explanation only. "
                    "It cannot block entries, exits, sizing, stops, targets, or risk controls."
                ),
            },
            {
                "agent": "Trade Memory",
                "status": f"Min {settings.ai.min_trade_score}",
                "confidence": 88,
                "summary": "Heavy model ranks technical, regime, anomaly, and memory context. Catalyst/news is attached for information only and cannot affect execution.",
            },
            {
                "agent": "Risk Governor",
                "status": "Clear" if risk_ok else "Blocked",
                "confidence": 92 if risk_ok else 88,
                "summary": (
                    f"Unlimited trades/day, ${settings.trading.fixed_trade_amount:.0f} per trade, "
                    f"{settings.trading.stop_loss_percent:.2f}% per-stock stop active, account daily-loss lock off, paper account only."
                    if settings.trading.max_trades_per_day <= 0
                    else f"Max {settings.trading.max_trades_per_day} trades/day, ${settings.trading.fixed_trade_amount:.0f} per trade, {settings.trading.stop_loss_percent:.2f}% per-stock stop active, account daily-loss lock off, paper account only."
                ),
            },
            {
                "agent": "Execution Router",
                "status": session_status["currentSession"],
                "confidence": 86 if session_status["canAutoTrade"] else 48,
                "summary": (
                    "Automatic stock entries use core-session execution only; open paper positions continue to be managed outside the entry window."
                ),
            },
        ]


STATE = DashboardState()


AUTH_SESSION_COOKIE = "agentic_session"
AUTH_DEVICE_COOKIE = "agentic_device"
AUTH_SESSION_COOKIE_MAX_AGE = 30 * 24 * 3600
AUTH_DEVICE_COOKIE_MAX_AGE = 365 * 24 * 3600
# Endpoints reachable without a session: the auth handshake itself and the
# health probe the supervisor/load tooling polls.
AUTH_PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/bootstrap",
    "/api/auth/logout",
}

_AUTH_SERVICE: AuthService | None = None
_AUTH_SERVICE_LOCK = threading.Lock()


def auth_service_instance() -> AuthService:
    global _AUTH_SERVICE
    if _AUTH_SERVICE is None:
        with _AUTH_SERVICE_LOCK:
            if _AUTH_SERVICE is None:
                _AUTH_SERVICE = AuthService()
    return _AUTH_SERVICE


def _auth_cookie_header(name: str, value: str, max_age: int) -> str:
    # Local HTTP app: HttpOnly + SameSite=Lax, no Secure flag (127.0.0.1).
    return f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"


def _schwab_trading_settings():
    """Config for the optional Accounts & Trading Schwab app.

    Separate keys and token file from the market-data profile, mirroring the
    dual-profile design the settings UI expects (memory: the trading refresh
    token expires weekly, independently of market data).
    """
    from dataclasses import replace
    return replace(
        settings.schwab,
        client_id=os.getenv("SCHWAB_TRADING_CLIENT_ID", ""),
        client_secret=os.getenv("SCHWAB_TRADING_CLIENT_SECRET", ""),
        token_path=os.getenv(
            "SCHWAB_TRADING_TOKEN_PATH",
            str(ARTIFACTS_DIR / "schwab_trading_token.json"),
        ),
    )


def _schwab_client_for_profile(profile: str) -> SchwabClient:
    if str(profile or "").strip().lower() == "trading":
        return SchwabClient(config=_schwab_trading_settings())
    return SchwabClient()


def _schwab_status_payload(**extra) -> dict:
    """Dual-profile status shape the frontend expects.

    The settings card reads schwabStatus.marketData.* / schwabStatus.trading.*;
    this backend build supports a single (market data) Schwab profile, so the
    flat connection status doubles as the marketData profile and the optional
    Accounts & Trading profile reports unconfigured (its buttons stay
    disabled). The Alpaca watchlist bar stream is reported honestly as not
    running - it was part of the lost backend build and is not rebuilt yet.
    """
    flat = SchwabClient().connection_status()
    try:
        trading_status = _schwab_client_for_profile("trading").connection_status()
    except Exception:
        trading_status = {"credentialsConfigured": False, "configured": False}
    payload = {
        **flat,
        "marketData": dict(flat),
        "trading": trading_status,
        "alpacaBarStream": ALPACA_STREAM.status(),
        "schwabStream": MARKET_STREAM.status(),
        "marketDataProvider": settings.market_data_provider,
        "callbackListening": callback_listener_status(),
    }
    payload.update(extra)
    return payload


MARKET_STREAM = SchwabMarketStream(
    client_factory=lambda: SchwabClient("trading"),
    chart_history_path=ARTIFACTS_DIR / "schwab_stream_chart_history.json.gz",
)


def _owner_alpaca_stream_credentials():
    try:
        connection = sqlite3.connect(str(DATABASE_PATH), timeout=10.0)
        try:
            row = connection.execute(
                "SELECT id FROM app_users WHERE is_active = 1 "
                "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return None
        credentials = auth_service_instance().get_provider_credentials(
            str(row[0]), "alpaca_market_data",
        )
        key = str(credentials.get("key_id") or "").strip()
        secret = str(credentials.get("secret_key") or "").strip()
        return (key, secret) if key and secret else None
    except Exception:
        return None


_SCHWAB_MD_HEALTH_CACHE = {"checked_at": 0.0, "healthy": False}


def _schwab_market_data_healthy() -> bool:
    """Cheap cached check: is the Schwab market-data profile serving?

    The live price line must have exactly ONE writer. Alpaca's IEX last is a
    thin slice of the tape and routinely sits cents away from Schwab's
    consolidated close, so publishing both made the line hop between the two
    several times a second. While Schwab is healthy, Alpaca stays silent and
    acts as pure failover.
    """
    now = time.monotonic()
    if now - _SCHWAB_MD_HEALTH_CACHE["checked_at"] > 15.0:
        healthy = False
        try:
            # LIVENESS, not credential presence. `SchwabClient().configured`
            # only proves a token file exists, so this returned True forever —
            # the Alpaca failover could never engage and the forming candle
            # stopped ticking whenever the Schwab socket was down (it only
            # advanced on the 1/min CHART_EQUITY bar). A stream is "serving"
            # only if it is connected AND delivered an event recently.
            status = MARKET_STREAM.status()
            if status.get("connected"):
                last_event_at = status.get("lastEventAt")
                if not last_event_at:
                    healthy = False
                else:
                    try:
                        stamp = datetime.fromisoformat(str(last_event_at).replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
                        healthy = age <= 45.0
                    except (TypeError, ValueError):
                        healthy = False
        except Exception:
            healthy = False
        _SCHWAB_MD_HEALTH_CACHE["healthy"] = healthy
        _SCHWAB_MD_HEALTH_CACHE["checked_at"] = now
    return _SCHWAB_MD_HEALTH_CACHE["healthy"]


ALPACA_STREAM = AlpacaBarStream(
    credentials_provider=_owner_alpaca_stream_credentials,
    publish=MARKET_STREAM._publish,
    # Single source of truth for "is Schwab actually serving?" — the helper
    # already requires connected AND a recent event, so no `or connected`
    # shortcut here (that shortcut is what made the gate always-true).
    primary_connected=_schwab_market_data_healthy,
    feed=settings.alpaca_data_feed,
)

_SSE_ACTIVE_SYMBOLS: dict[str, int] = {}
_SSE_ACTIVE_LOCK = threading.Lock()


def _sse_track_symbols(symbols, delta: int) -> None:
    """Reference-count SSE subscriber symbols; drive the Alpaca socket set."""
    with _SSE_ACTIVE_LOCK:
        for symbol in symbols:
            count = _SSE_ACTIVE_SYMBOLS.get(symbol, 0) + delta
            if count > 0:
                _SSE_ACTIVE_SYMBOLS[symbol] = count
            else:
                _SSE_ACTIVE_SYMBOLS.pop(symbol, None)
        active = set(_SSE_ACTIVE_SYMBOLS)
    ALPACA_STREAM.set_symbols(active)


class ApiHandler(BaseHTTPRequestHandler):
    def _request_cookies(self) -> dict:
        header = str(self.headers.get("Cookie", "") or "")
        cookies: dict[str, str] = {}
        for part in header.split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                cookies[name.strip()] = value.strip()
        return cookies

    def _session_user(self) -> dict | None:
        cookies = self._request_cookies()
        user = auth_service_instance().user_for_session(
            cookies.get(AUTH_SESSION_COOKIE),
            cookies.get(AUTH_DEVICE_COOKIE),
        )
        if user is not None:
            return user
        # Restored from the pre-incident build: LOCAL_AUTO_LOGIN_EMAIL signs
        # every LOOPBACK request in as that user, so a backend restart never
        # dumps the trader (or local tooling) onto the login page mid-session.
        # Opt-in via .env only, never for remote clients; anyone on this
        # machine's loopback already controls the database file — the same
        # reasoning as the sole-admin device bypass in _finish_login. main()
        # prints a boot warning whenever this is active.
        auto_login_email = str(os.getenv("LOCAL_AUTO_LOGIN_EMAIL", "") or "").strip()
        if not auto_login_email:
            return None
        client_ip = str(self.client_address[0] if self.client_address else "")
        is_loopback = client_ip in {"127.0.0.1", "::1"} or client_ip.startswith("127.")
        if not is_loopback:
            return None
        try:
            return auth_service_instance().get_user_by_email(auto_login_email)
        except Exception:
            # A broken auto-login must degrade to the normal sign-in flow,
            # never take the API down.
            return None

    def _require_session_user(self) -> dict | None:
        """Return the authenticated user, or send 401 and return None."""
        try:
            user = self._session_user()
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return None
        if user is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Sign in to continue."})
            return None
        return user

    def _api_gate_passed(self, parsed_path: str) -> bool:
        """Session-gate every /api route except the public auth handshake."""
        if not parsed_path.startswith("/api/") or parsed_path in AUTH_PUBLIC_API_PATHS:
            return True
        return self._require_session_user() is not None

    def _send_json_with_cookies(self, status: HTTPStatus, payload: dict, cookie_headers: list) -> None:
        response = json.dumps(_serialize_value(payload)).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        for header_value in cookie_headers:
            self.send_header("Set-Cookie", header_value)
        self.end_headers()
        self.wfile.write(response)

    def _finish_login(self, auth: AuthService, user: dict) -> None:
        """Device authorization + session issue, shared by login and bootstrap."""
        cookies = self._request_cookies()
        decision = auth.authorize_login_device(
            user,
            device_token=cookies.get(AUTH_DEVICE_COOKIE, ""),
            user_agent=str(self.headers.get("User-Agent", "") or ""),
            ip_address=str(self.client_address[0] if self.client_address else ""),
        )
        if not decision["approved"]:
            client_ip = str(self.client_address[0] if self.client_address else "")
            is_loopback = client_ip in {"127.0.0.1", "::1"} or client_ip.startswith("127.")
            if user.get("isAdmin") and is_loopback:
                # A locked-out sole administrator cannot approve their own
                # device. Anyone signing in as the admin from this machine's
                # loopback already controls the database file, so the device
                # gate adds nothing here; remote/mobile devices stay gated.
                auth.decide_device_request(user, decision["device"]["id"], "approve")
            else:
                self._send_json_with_cookies(
                    HTTPStatus.FORBIDDEN,
                    {
                        "error": "This device is waiting for admin approval. Approve it from an already signed-in device, then sign in again.",
                        "devicePending": True,
                    },
                    [_auth_cookie_header(AUTH_DEVICE_COOKIE, decision["deviceToken"], AUTH_DEVICE_COOKIE_MAX_AGE)],
                )
                return
        session_token = auth.create_session(user, device_id=decision["device"]["id"])
        self._send_json_with_cookies(
            HTTPStatus.OK,
            {"user": user},
            [
                _auth_cookie_header(AUTH_SESSION_COOKIE, session_token, AUTH_SESSION_COOKIE_MAX_AGE),
                _auth_cookie_header(AUTH_DEVICE_COOKIE, decision["deviceToken"], AUTH_DEVICE_COOKIE_MAX_AGE),
            ],
        )

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/auth/status":
                auth = auth_service_instance()
                bootstrap_required = auth.bootstrap_required()
                user = None if bootstrap_required else self._session_user()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "bootstrapRequired": bootstrap_required,
                        "bootstrapRequiresToken": bool(os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()),
                        "user": user,
                    },
                )
                return
            if not self._api_gate_passed(parsed.path):
                return
            if parsed.path == "/api/user/api-keys":
                user = self._require_session_user()
                if user is None:
                    return
                self._send_json(HTTPStatus.OK, auth_service_instance().provider_summary(user))
                return
            if parsed.path == "/api/admin/users":
                user = self._require_session_user()
                if user is None:
                    return
                self._send_json(HTTPStatus.OK, {"users": auth_service_instance().list_users(user)})
                return
            if parsed.path == "/api/admin/devices":
                user = self._require_session_user()
                if user is None:
                    return
                cookies = self._request_cookies()
                self._send_json(
                    HTTPStatus.OK,
                    auth_service_instance().list_devices(user, cookies.get(AUTH_DEVICE_COOKIE, "")),
                )
                return
            if parsed.path == "/api/health":
                runtime_health = (
                    STATE._runtime_health_payload()
                    if hasattr(STATE, "_runtime_health_payload")
                    else {"enabled": False, "status": "Healthy", "components": {}}
                )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": runtime_health.get("status") not in {"Error", "Degraded"},
                        "runtime": runtime_health,
                        # Timing-only diagnostics make a delayed live feed
                        # distinguishable from a slow browser without exposing
                        # credentials, account data, or order information.
                        "marketStream": MARKET_STREAM.status(),
                        "alpacaStream": ALPACA_STREAM.status(),
                    },
                )
                return
            if parsed.path in {"/api/dashboard", "/api/status"}:
                # Poll runs every 5s; skip re-shipping ~1MB of scanner history
                # the client already holds (see dashboard_payload_for_client).
                dashboard_query = parse_qs(parsed.query)
                held_history = str(dashboard_query.get("scannerHistoryVersion", [""])[0]).strip()
                compact = str(dashboard_query.get("compact", [""])[0]).strip().lower() in {
                    "1", "true", "yes",
                }
                self._send_json(
                    HTTPStatus.OK,
                    dashboard_payload_for_client(
                        STATE.dashboard_payload(), held_history, compact=compact,
                    ),
                )
                return
            if parsed.path == "/api/mag7-oi-walls":
                query = parse_qs(parsed.query)
                force = str(query.get("force", [""])[0]).strip().lower() in {"1", "true", "yes"}
                self._send_json(HTTPStatus.OK, STATE.mag7_oi_wall_payload(force=force))
                return
            if parsed.path == "/api/oi-finder":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", ["AAPL"])[0]
                force = str(query.get("force", [""])[0]).strip().lower() in {"1", "true", "yes"}
                self._send_json(HTTPStatus.OK, STATE.oi_finder_payload(symbol, force=force))
                return
            if parsed.path == "/api/oi-finder-chain":
                # Compact chain feed: the frontend expects the same shape as
                # /api/oi-finder and treats this endpoint as the fast path.
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", ["AAPL"])[0]
                force = str(query.get("force", [""])[0]).strip().lower() in {"1", "true", "yes"}
                initial_paint = str(query.get("initial", [""])[0]).strip().lower() in {"1", "true", "yes"}
                self._send_json(
                    HTTPStatus.OK,
                    STATE.oi_finder_payload(
                        symbol,
                        force=force,
                        compact=True,
                        initial_paint=initial_paint,
                    ),
                )
                return
            if parsed.path == "/api/ticker-strip":
                query = parse_qs(parsed.query)
                symbol = str(query.get("symbol", [""])[0]).strip().upper()
                strip = {"symbol": symbol}
                if symbol:
                    try:
                        quote = SchwabClient().get_quotes([symbol]).get(symbol) or {}
                        strip.update({
                            "lastPrice": quote.get("last_price"),
                            "change": quote.get("change"),
                            "changePct": quote.get("change_pct"),
                            # The strip recomputes change/% from live ticks
                            # using closePrice; without it the day-change
                            # froze between the 15s polls. `name` fills the
                            # company label that fell back to the symbol.
                            "closePrice": quote.get("close_price"),
                            "name": quote.get("description") or "",
                        })
                    except Exception:
                        pass
                self._send_json(HTTPStatus.OK, strip)
                return
            if parsed.path == "/api/watchlist-quotes":
                query = parse_qs(parsed.query)
                raw_symbols = str(query.get("symbols", [""])[0])
                symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
                # A 397-symbol watchlist polling every 8s is 4 chunked Schwab
                # quote calls per request (~30/min) — enough to hit provider
                # rate limits and stall the panel. Serve a short shared cache
                # keyed on the exact symbol set; every panel showing the same
                # list then costs one upstream fetch per window.
                rows = []
                if symbols:
                    cache_key = ",".join(symbols)
                    now_monotonic = time.monotonic()
                    cache = getattr(STATE, "_watchlist_quote_cache", None)
                    if not isinstance(cache, dict):
                        cache = {}
                        STATE._watchlist_quote_cache = cache
                    cached = cache.get(cache_key)
                    if cached and now_monotonic - cached[0] < 5.0:
                        rows = cached[1]
                    else:
                        try:
                            quotes = SchwabClient().get_quotes(symbols)
                        except Exception:
                            quotes = {}
                        for symbol in symbols:
                            quote = quotes.get(symbol) or {}
                            rows.append({
                                "symbol": symbol,
                                "lastPrice": quote.get("last_price"),
                                "change": quote.get("change"),
                                "changePct": quote.get("change_pct"),
                                "volume": quote.get("volume"),
                            })
                        # Only cache a response that actually carries prices, so
                        # a transient provider failure cannot pin an empty table
                        # for the whole window.
                        if any(row["lastPrice"] is not None for row in rows):
                            cache[cache_key] = (now_monotonic, rows)
                        if len(cache) > 12:
                            for stale in sorted(cache, key=lambda key: cache[key][0])[:-12]:
                                cache.pop(stale, None)
                self._send_json(HTTPStatus.OK, {"rows": rows})
                return
            if parsed.path == "/api/live-chart-quotes":
                query = parse_qs(parsed.query)
                raw_symbols = str(query.get("symbols", [""])[0])
                # This endpoint is exclusively the lightweight safety path
                # for charts currently on screen. Keep it small and use the
                # same trading-profile quote source as the Schwab Level-1
                # socket; the general watchlist endpoint remains separately
                # cached for large symbol tables.
                symbols = list(dict.fromkeys(
                    item.strip().upper()
                    for item in raw_symbols.split(",")
                    if item.strip()
                ))[:8]
                rows = []
                if symbols:
                    try:
                        quotes = SchwabClient("trading").get_quotes(symbols)
                        if not quotes:
                            quotes = SchwabClient().get_quotes(symbols)
                    except Exception:
                        quotes = {}
                    for symbol in symbols:
                        quote = quotes.get(symbol) or {}
                        rows.append({
                            "symbol": symbol,
                            "lastPrice": quote.get("last_price"),
                        })
                self._send_json(HTTPStatus.OK, {"rows": rows})
                return
            if parsed.path == "/api/chart-grids":
                grids_path = ARTIFACTS_DIR / "chart_grids.json"
                grids = {}
                try:
                    if grids_path.exists():
                        parsed_grids = json.loads(grids_path.read_text(encoding="utf-8"))
                        if isinstance(parsed_grids, dict):
                            grids = parsed_grids
                except Exception:
                    grids = {}
                self._send_json(HTTPStatus.OK, {"grids": grids})
                return
            if parsed.path == "/api/live-option-stream":
                # Tick-by-tick option chain: subscribe the VISIBLE contracts of
                # one underlying so the open chain table can merge live quotes
                # instead of waiting for the 15s REST poll. Contract symbols
                # come from the chain rows the browser is already showing, so
                # the subscription is always scoped to what is on screen and
                # never approaches the streamer's 300-option ceiling.
                # Packets ride the SAME /api/live-market-stream pump: the event
                # router already routes type=="option" by top-level underlying,
                # so no second SSE connection is needed.
                query = parse_qs(parsed.query)
                underlying = str(query.get("symbol", [""])[0]).strip().upper()
                raw_contracts = str(query.get("contracts", [""])[0])
                contracts = [item.strip().upper() for item in raw_contracts.split(",") if item.strip()]
                if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", underlying):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Enter a valid ticker symbol."})
                    return
                MARKET_STREAM.watch(underlying, contracts, replace_options=True)
                status = MARKET_STREAM.status()
                self._send_json(HTTPStatus.OK, {
                    "symbol": underlying,
                    "requested": len(contracts),
                    "subscribed": int(status.get("optionSymbols") or 0),
                    "streamConnected": bool(status.get("connected")),
                    # Surfaced so the browser can explain a silent chain: the
                    # option feed needs the trading-profile token, exactly like
                    # live candles.
                    "streamError": str(status.get("lastError") or ""),
                })
                return
            if parsed.path == "/api/live-market-stream":
                query = parse_qs(parsed.query)
                raw_symbols = str(query.get("symbols", [""])[0])
                symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
                # Arm both sources: Schwab (primary, needs the trading-profile
                # token) and the Alpaca fallback socket. Whichever is alive
                # publishes into the shared event queue this pump drains.
                acquired = MARKET_STREAM.acquire_equities(symbols)
                _sse_track_symbols(symbols, +1)
                cursor = event_stream_cursor(
                    self.headers.get("Last-Event-ID"),
                    MARKET_STREAM.latest_sequence(),
                )
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        cursor, events = MARKET_STREAM.wait_for_events(cursor, symbols, timeout=15.0)
                        if not events:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        chunks = []
                        for event in events:
                            packet = {
                                "symbol": event.get("symbol", ""),
                                "data": event.get("data") or {},
                                "receivedAt": event.get("receivedAt", ""),
                            }
                            if event.get("type") == "option":
                                packet["underlying"] = event.get("underlying", "")
                            chunks.append(
                                f"id: {event.get('sequence', 0)}\n"
                                f"event: {event.get('type', 'status')}\n"
                                f"data: {json.dumps(packet)}\n\n"
                            )
                        self.wfile.write("".join(chunks).encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    MARKET_STREAM.release_equities(acquired)
                    _sse_track_symbols(symbols, -1)
                return
            if parsed.path == "/api/oi-finder-chart":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", ["AAPL"])[0]
                if str(query.get("historyStatus", [""])[0]).lower() in {"1", "true", "yes"}:
                    self._send_json(HTTPStatus.OK, STATE.oi_finder_chart_history_status(symbol))
                    return
                initial_paint = str(query.get("initial", [""])[0]).lower() in {"1", "true", "yes"}
                include_study_seed = str(query.get("initialStudy", [""])[0]).lower() in {
                    "1", "true", "yes",
                }
                prefetch = str(query.get("prefetch", [""])[0]).lower() in {"1", "true", "yes"}
                refresh = str(query.get("refresh", [""])[0]).lower() in {"1", "true", "yes"}
                try:
                    since_epoch = float(query.get("since", ["0"])[0])
                except (TypeError, ValueError):
                    since_epoch = 0.0
                self._send_json(HTTPStatus.OK, STATE.oi_finder_chart_payload(
                    symbol,
                    initial_paint=initial_paint,
                    since_epoch=since_epoch,
                    prefetch=prefetch,
                    refresh=refresh,
                    include_study_seed=include_study_seed,
                ))
                return
            if parsed.path == "/api/learning-status":
                self._send_json(HTTPStatus.OK, STATE._learning_status_payload())
                return
            if parsed.path == "/api/premarket-plan":
                self._send_json(HTTPStatus.OK, STATE.premarket_plan_payload(refresh=True))
                return
            if parsed.path == "/api/chart":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", ["AAPL"])[0]
                timeframe = query.get("timeframe", ["5Min"])[0]
                self._send_json(HTTPStatus.OK, STATE.chart_payload(symbol, timeframe))
                return
            if parsed.path == "/api/why-not-traded":
                query = parse_qs(parsed.query)
                symbol = query.get("symbol", ["AAPL"])[0]
                self._send_json(HTTPStatus.OK, STATE.why_not_traded(symbol))
                return
            if parsed.path == "/api/scan-diagnostics":
                query = parse_qs(parsed.query)
                raw_symbols = query.get("symbols", [""])[0]
                symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
                self._send_json(HTTPStatus.OK, STATE.scan_diagnostics(symbols))
                return
            if parsed.path == "/api/backtest":
                query = parse_qs(parsed.query)
                raw_symbols = query.get("symbols", [""])[0]
                symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
                payload = STATE.start_backtest_job(symbols) if symbols else STATE.dashboard_payload()
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/news-feed":
                self._send_json(HTTPStatus.OK, STATE.load_news())
                return
            if parsed.path == "/api/forex-factory-us-news":
                query = parse_qs(parsed.query)
                force = str(query.get("force", [""])[0]).strip().lower() in {"1", "true", "yes"}
                self._send_json(HTTPStatus.OK, STATE.forex_factory_us_news_payload(force=force))
                return
            if parsed.path == "/api/earnings-calendar":
                query = parse_qs(parsed.query)
                try:
                    days = int(query.get("days", [EARNINGS_CALENDAR_DEFAULT_DAYS])[0])
                except (TypeError, ValueError):
                    days = EARNINGS_CALENDAR_DEFAULT_DAYS
                force = str(query.get("force", [""])[0]).strip().lower() in {"1", "true", "yes"}
                self._send_json(HTTPStatus.OK, STATE.earnings_calendar_payload(days=days, force=force))
                return
            if parsed.path == "/api/accounts":
                self._send_json(HTTPStatus.OK, {"accounts": STATE.available_accounts()})
                return
            if parsed.path == "/api/schwab/auth-url":
                client = SchwabClient()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "configured": bool(settings.schwab.client_id),
                        "authorizationUrl": client.authorization_url() if settings.schwab.client_id else "",
                        "redirectUri": settings.schwab.redirect_uri,
                        "marketDataProvider": settings.market_data_provider,
                    },
                )
                return
            if parsed.path == "/api/schwab/status":
                self._send_json(HTTPStatus.OK, _schwab_status_payload())
                return
            if parsed.path == "/api/watchlist":
                self._send_json(HTTPStatus.OK, {"tickers": settings.scanner.default_universe})
                return
            if parsed.path == "/api/mag7-scanner-watchlist":
                self._send_json(HTTPStatus.OK, {"tickers": STATE.mag7_scanner_watchlist})
                return
            if parsed.path == "/api/option-watchlist":
                self._send_json(HTTPStatus.OK, {"tickers": STATE.option_watchlist})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {parsed.path}"})
        except AuthorizationError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except AuthenticationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            body = self._read_json_body()

            if parsed.path == "/api/auth/bootstrap":
                auth = auth_service_instance()
                configured_token = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()
                supplied_token = str(body.get("setupToken", "") or "").strip()
                if configured_token and supplied_token != configured_token:
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "The admin setup token is incorrect."})
                    return
                user = auth.bootstrap_owner(
                    str(body.get("email", "") or ""),
                    str(body.get("password", "") or ""),
                    str(body.get("displayName", "") or ""),
                )
                self._finish_login(auth, user)
                return
            if parsed.path == "/api/auth/login":
                auth = auth_service_instance()
                user = auth.verify_credentials(
                    str(body.get("email", "") or ""),
                    str(body.get("password", "") or ""),
                )
                self._finish_login(auth, user)
                return
            if parsed.path == "/api/auth/logout":
                cookies = self._request_cookies()
                auth_service_instance().logout(cookies.get(AUTH_SESSION_COOKIE))
                self._send_json_with_cookies(
                    HTTPStatus.OK,
                    {"ok": True},
                    [_auth_cookie_header(AUTH_SESSION_COOKIE, "", 0)],
                )
                return
            if not self._api_gate_passed(parsed.path):
                return
            if parsed.path == "/api/auth/change-password":
                user = self._require_session_user()
                if user is None:
                    return
                auth_service_instance().change_password(
                    user,
                    current_password=str(body.get("currentPassword", "") or ""),
                    new_password=str(body.get("newPassword", "") or ""),
                )
                # change_password signs out every session; clear this one too.
                self._send_json_with_cookies(
                    HTTPStatus.OK,
                    {"ok": True},
                    [_auth_cookie_header(AUTH_SESSION_COOKIE, "", 0)],
                )
                return
            if parsed.path == "/api/user/api-keys":
                user = self._require_session_user()
                if user is None:
                    return
                auth = auth_service_instance()
                provider = str(body.get("provider", "") or "")
                values = {
                    "key_id": body.get("keyId"),
                    "secret_key": body.get("secretKey"),
                    "access_token": body.get("accessToken"),
                    "client_id": body.get("clientId"),
                    "client_secret": body.get("clientSecret"),
                }
                auth.save_provider_credentials(user, provider, values)
                # A newly saved Alpaca key must feed the chart fallback
                # immediately, without waiting for a server restart.
                STATE._owner_alpaca_client_cache = None
                self._send_json(HTTPStatus.OK, auth.provider_summary(user))
                return
            if parsed.path == "/api/admin/users":
                user = self._require_session_user()
                if user is None:
                    return
                created = auth_service_instance().create_user(
                    str(body.get("email", "") or ""),
                    str(body.get("temporaryPassword", "") or ""),
                    actor=user,
                    display_name=str(body.get("displayName", "") or ""),
                    role=str(body.get("role", "user") or "user"),
                )
                self._send_json(HTTPStatus.OK, {"user": created})
                return
            if parsed.path == "/api/admin/device-requests":
                user = self._require_session_user()
                if user is None:
                    return
                result = auth_service_instance().decide_device_request(
                    user,
                    str(body.get("deviceId", "") or ""),
                    str(body.get("action", "") or ""),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/admin/devices/revoke":
                user = self._require_session_user()
                if user is None:
                    return
                cookies = self._request_cookies()
                result = auth_service_instance().revoke_device(
                    user,
                    str(body.get("deviceId", "") or ""),
                    current_device_token=cookies.get(AUTH_DEVICE_COOKIE, ""),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if parsed.path == "/api/chart-grids":
                grids = body.get("grids")
                if not isinstance(grids, dict):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "grids object is required."})
                    return
                grids_path = ARTIFACTS_DIR / "chart_grids.json"
                grids_path.parent.mkdir(parents=True, exist_ok=True)
                grids_path.write_text(json.dumps(grids), encoding="utf-8")
                self._send_json(HTTPStatus.OK, {"ok": True, "count": len(grids)})
                return
            if parsed.path == "/api/option-roi-estimate":
                # The calculator computes a local gamma estimate whenever the
                # server returns no results, so an empty list keeps the ROI
                # view fully functional without the lost server-side model.
                self._send_json(HTTPStatus.OK, {"results": []})
                return
            if parsed.path == "/api/mag7-signal-scanner-config":
                config_path = ARTIFACTS_DIR / "mag7_signal_scanner_config.json"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                saved = {}
                try:
                    if config_path.exists():
                        saved = json.loads(config_path.read_text(encoding="utf-8"))
                        if not isinstance(saved, dict):
                            saved = {}
                except Exception:
                    saved = {}
                for key in ("fourHourVolumeEnabled", "oneHourCloseEnabled"):
                    if key in body:
                        saved[key] = bool(body.get(key))
                config_path.write_text(json.dumps(saved), encoding="utf-8")
                payload = STATE.dashboard_payload()
                payload["mag7SignalScannerConfig"] = saved
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/mag7-signal-scan":
                STATE.action_message = (
                    "The MAG7 TOS signal-scan engine is not available in this backend build; "
                    "it is being restored."
                )
                self._send_json(HTTPStatus.OK, STATE.dashboard_payload())
                return
            if parsed.path == "/api/earnings-calendar/ocr":
                try:
                    payload = STATE.analyze_earnings_screenshot(
                        image_data=body.get("imageData"),
                        week_start=body.get("weekStart"),
                    )
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/earnings-calendar/manual-import":
                try:
                    payload = STATE.save_manual_earnings_imports(body.get("rows"))
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/earnings-calendar/manual-imports/clear":
                self._send_json(HTTPStatus.OK, STATE.clear_manual_earnings_imports())
                return
            if parsed.path == "/api/learning-cycle":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.start_learning_cycle(
                        force_training=bool(body.get("forceTraining", False)),
                    ),
                )
                return
            if parsed.path == "/api/scan":
                requested_universe = str(body.get("universe", "watchlist") or "watchlist").strip().lower()
                raw_symbols = body.get("symbols") or []
                if isinstance(raw_symbols, str):
                    symbols = [item.strip().upper() for item in raw_symbols.split(",") if item.strip()]
                elif isinstance(raw_symbols, list):
                    symbols = [str(item).strip().upper() for item in raw_symbols if str(item).strip()]
                else:
                    symbols = []
                if requested_universe == "mag7":
                    self._send_json(HTTPStatus.OK, STATE.start_scan_job(STATE.mag7_scanner_watchlist, "MAG7-Watchlist Options"))
                    return
                if symbols:
                    self._send_json(HTTPStatus.OK, STATE.start_scan_job(symbols, "Custom"))
                    return
                self._send_json(HTTPStatus.OK, STATE.start_scan_job())
                return
            if parsed.path == "/api/option-scan":
                self._send_json(HTTPStatus.OK, STATE.scan_options(create_plans=STATE.option_bot_state == "Running"))
                return
            if parsed.path == "/api/oi-scan":
                requested_universe = str(body.get("universe", "mag7") or "mag7").strip().lower()
                if requested_universe in {"watchlist", "both"}:
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {
                            "error": (
                                "Watchlist OI scanning is disabled. The OI scanner uses the saved Mag7 "
                                "watchlist and direct Schwab/TOS option-chain requests."
                            )
                        },
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    STATE.start_oi_scan_job(
                        symbols=STATE._mag7_oi_underlyings(),
                        scan_label="MAG7 OI Scanner",
                    ),
                )
                return
            if parsed.path == "/api/oi-scanner-auto-control":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.set_oi_scanner_auto_enabled(bool(body.get("enabled", False))),
                )
                return
            if parsed.path == "/api/scanner-auto-control":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.set_stock_scanner_auto_enabled(bool(body.get("enabled", False))),
                )
                return
            if parsed.path == "/api/option-chain-liquidity-scan":
                raw_symbols = body.get("symbols") or []
                if isinstance(raw_symbols, str):
                    symbols = [item.strip().upper() for item in raw_symbols.replace("\n", ",").split(",") if item.strip()]
                elif isinstance(raw_symbols, list):
                    symbols = [str(item).strip().upper() for item in raw_symbols if str(item).strip()]
                else:
                    symbols = []
                min_delta = STATE._parse_numeric_guardrail(str(body.get("minDelta", body.get("min_delta", "0.2"))), 0.2)
                min_expected_move = STATE._parse_numeric_guardrail(
                    str(body.get("minExpectedMove", body.get("min_expected_move", "0"))),
                    0.0,
                )
                max_per_symbol = int(STATE._parse_numeric_guardrail(str(body.get("maxPerSymbol", body.get("max_per_symbol", "5"))), 5) or 5)
                self._send_json(
                    HTTPStatus.OK,
                    STATE.scan_option_chain_liquidity(
                        symbols=symbols or None,
                        min_delta=float(min_delta or 0.2),
                        min_expected_move=float(min_expected_move or 0.0),
                        allow_zero_dte_after_hours=bool(body.get("allowZeroDteAfterHours", False)),
                        max_per_symbol=max_per_symbol,
                    ),
                )
                return
            if parsed.path == "/api/execute-best-trade":
                self._send_json(HTTPStatus.OK, STATE.execute_best_trade())
                return
            if parsed.path == "/api/execute-all-trades":
                self._send_json(HTTPStatus.OK, STATE.execute_all_trades())
                return
            if parsed.path == "/api/close-position":
                symbol = str(body.get("symbol", "")).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Symbol is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.close_position(symbol))
                return
            if parsed.path == "/api/close-all-positions":
                self._send_json(HTTPStatus.OK, STATE.close_all_positions())
                return
            if parsed.path == "/api/close-option-position":
                option_symbol = str(body.get("optionSymbol") or body.get("symbol") or "").strip().upper()
                if not option_symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "optionSymbol is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.close_option_position(option_symbol))
                return
            if parsed.path == "/api/close-all-option-positions":
                self._send_json(HTTPStatus.OK, STATE.close_all_option_positions())
                return
            if parsed.path == "/api/manage-option-positions":
                self._send_json(HTTPStatus.OK, STATE.manage_option_positions_now())
                return
            if parsed.path == "/api/cancel-stale-option-buy-orders":
                self._send_json(HTTPStatus.OK, STATE.cancel_stale_option_buy_orders())
                return
            if parsed.path == "/api/account-select":
                profile_id = str(body.get("profileId", "")).strip()
                if not profile_id:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "profileId is required."})
                    return
                payload = STATE.select_account(profile_id)
                if "error" in payload:
                    self._send_json(HTTPStatus.BAD_REQUEST, payload)
                    return
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/risk-settings":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.update_risk_settings(
                        trade_amount=body.get("tradeAmount"),
                        daily_trade_amount=body.get("dailyTradeAmount"),
                        stop_loss_percent=body.get("stopLossPercent"),
                        stop_loss_amount=body.get("stopLossAmount"),
                        first_profit_target_percent=body.get("firstProfitTargetPercent"),
                    ),
                )
                return
            if parsed.path == "/api/scanner-storage-settings":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.update_scanner_storage_settings(
                        history_retention_days=body.get("historyRetentionDays"),
                    ),
                )
                return
            if parsed.path == "/api/option-risk-settings":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.update_option_risk_settings(
                        daily_trade_amount=body.get("dailyTradeAmount"),
                        trade_amount=body.get("tradeAmount"),
                        contract_quantity=body.get("contractQuantity"),
                        stop_loss_percent=body.get("stopLossPercent"),
                        first_profit_target_percent=body.get("firstProfitTargetPercent"),
                        first_profit_target_cons=body.get("firstProfitTargetCons"),
                        first_profit_target_sell_mode=body.get("firstProfitTargetSellMode"),
                        first_profit_target_sell_value=body.get("firstProfitTargetSellValue"),
                        runner_lock_step_percent=body.get("runnerLockStepPercent"),
                    ),
                )
                return
            if parsed.path == "/api/option-bot-settings":
                self._send_json(
                    HTTPStatus.OK,
                    STATE.update_option_bot_config(
                        contract_policy=body.get("contractPolicy"),
                        approval_mode=body.get("approvalMode"),
                        spread_filter=body.get("spreadFilter"),
                        delta_target=body.get("deltaTarget"),
                        expected_move=body.get("expectedMove"),
                        watchlist_source=body.get("watchlistSource"),
                    ),
                )
                return
            if parsed.path == "/api/bot-control":
                requested_state = str(body.get("state", "Stopped")).strip().title()
                if requested_state not in {"Running", "Paused", "Stopped"}:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "State must be Running, Paused, or Stopped."})
                    return
                self._send_json(HTTPStatus.OK, STATE.set_bot_state(requested_state))
                return
            if parsed.path == "/api/option-bot-control":
                requested_state = str(body.get("state", "Stopped")).strip().title()
                if requested_state not in {"Running", "Paused", "Stopped"}:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "State must be Running, Paused, or Stopped."})
                    return
                self._send_json(HTTPStatus.OK, STATE.set_option_bot_state(requested_state))
                return
            if parsed.path == "/api/option-paper-trades":
                self._send_json(
                    HTTPStatus.CREATED,
                    STATE.log_option_paper_trade(
                        underlying_symbol=body.get("underlyingSymbol", ""),
                        option_symbol=body.get("optionSymbol", ""),
                        structure=body.get("structure", "Only Long Call"),
                        quantity=body.get("quantity", 1),
                        entry_price=body.get("entryPrice"),
                        stop_price=body.get("stopPrice"),
                        target_price=body.get("targetPrice"),
                        notes=body.get("notes", ""),
                    ),
                )
                return
            if parsed.path == "/api/backtest":
                symbols = [item.strip().upper() for item in body.get("symbols", []) if str(item).strip()]
                if not symbols:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Provide at least one symbol for backtesting."})
                    return
                self._send_json(
                    HTTPStatus.OK,
                    STATE.start_backtest_job(
                        symbols,
                        body.get("startDate"),
                        body.get("endDate"),
                    ),
                )
                return
            if parsed.path == "/api/news-feed":
                symbols = [item.strip().upper() for item in body.get("symbols", []) if str(item).strip()]
                self._send_json(HTTPStatus.OK, STATE.load_news(symbols or None))
                return
            if parsed.path == "/api/scheduler":
                enabled = bool(body.get("enabled", False))
                interval = body.get("intervalSeconds")
                self._send_json(HTTPStatus.OK, STATE.set_scheduler(enabled, interval))
                return
            if parsed.path == "/api/schwab/token":
                received_url = str(body.get("authorizationResponseUrl", "")).strip()
                if not received_url:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Use the Re-authenticate button so the local callback can securely complete Schwab OAuth."},
                    )
                    return
                token = _schwab_client_for_profile(
                    str(body.get("profile", "market_data") or "market_data"),
                ).exchange_authorization_response(received_url)
                self._send_json(
                    HTTPStatus.OK,
                    _schwab_status_payload(
                        status="connected",
                        hasAccessToken=bool(token.get("access_token")),
                        hasRefreshToken=bool(token.get("refresh_token")),
                    ),
                )
                return
            if parsed.path == "/api/schwab/settings":
                client_id = str(body.get("clientId", "")).strip() or settings.schwab.client_id
                client_secret = str(body.get("clientSecret", "")).strip() or settings.schwab.client_secret
                if not client_id or not client_secret:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Schwab app key and secret are required."})
                    return
                set_key(str(ENV_PATH), "SCHWAB_CLIENT_ID", client_id, quote_mode="never")
                set_key(str(ENV_PATH), "SCHWAB_CLIENT_SECRET", client_secret, quote_mode="never")
                os.environ["SCHWAB_CLIENT_ID"] = client_id
                os.environ["SCHWAB_CLIENT_SECRET"] = client_secret
                settings.schwab.client_id = client_id
                settings.schwab.client_secret = client_secret
                # The optional Accounts & Trading app keeps its own key pair;
                # blank fields leave the stored pair unchanged.
                trading_client_id = str(body.get("tradingClientId", "")).strip()
                trading_client_secret = str(body.get("tradingClientSecret", "")).strip()
                if trading_client_id:
                    set_key(str(ENV_PATH), "SCHWAB_TRADING_CLIENT_ID", trading_client_id, quote_mode="never")
                    os.environ["SCHWAB_TRADING_CLIENT_ID"] = trading_client_id
                if trading_client_secret:
                    set_key(str(ENV_PATH), "SCHWAB_TRADING_CLIENT_SECRET", trading_client_secret, quote_mode="never")
                    os.environ["SCHWAB_TRADING_CLIENT_SECRET"] = trading_client_secret
                self._send_json(HTTPStatus.OK, _schwab_status_payload(saved=True))
                return
            if parsed.path == "/api/schwab/oauth/start":
                query = parse_qs(parsed.query)
                client = _schwab_client_for_profile(str(query.get("profile", ["market_data"])[0]))
                if not client.config.client_id or not client.config.client_secret:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Save the Schwab app key and secret first."})
                    return
                authorization_url = client.begin_authorization()
                start_callback_listener()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authorizationUrl": authorization_url,
                        "redirectUri": client.config.redirect_uri,
                        "callbackListening": callback_listener_status(),
                        "tokenManager": "schwab-py",
                    },
                )
                return
            if parsed.path == "/api/schwab/test-connection":
                query = parse_qs(parsed.query)
                profile = str(query.get("profile", ["market_data"])[0]).strip().lower()
                section = "trading" if profile == "trading" else "marketData"
                client = _schwab_client_for_profile(profile)
                try:
                    verification = client.test_connection()
                except Exception as exc:
                    failure = _schwab_status_payload(error=str(exc), code="schwab_connection_failed")
                    failure[section]["connected"] = False
                    self._send_json(HTTPStatus.BAD_GATEWAY, failure)
                    return
                success = _schwab_status_payload(**verification)
                # The connected/verifiedAt badge lives on the tested profile.
                success[section].update(verification)
                self._send_json(HTTPStatus.OK, success)
                return
            if parsed.path == "/api/watchlist":
                if "ticker" in body:
                    symbol = str(body.get("ticker", "")).strip().upper()
                    if not symbol:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "ticker is required."})
                        return
                    self._send_json(HTTPStatus.CREATED, STATE.add_watchlist_symbol(symbol))
                    return
                symbols = body.get("symbols", [])
                if isinstance(symbols, str):
                    symbols = [item.strip().upper() for item in symbols.replace("\n", ",").split(",") if item.strip()]
                self._send_json(HTTPStatus.OK, STATE.replace_watchlist(symbols))
                return
            if parsed.path == "/api/option-watchlist":
                if "ticker" in body:
                    symbol = str(body.get("ticker", "")).strip().upper()
                    if not symbol:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "ticker is required."})
                        return
                    self._send_json(HTTPStatus.CREATED, STATE.add_option_watchlist_symbol(symbol))
                    return
                symbols = body.get("symbols", [])
                if isinstance(symbols, str):
                    symbols = [item.strip().upper() for item in symbols.replace("\n", ",").split(",") if item.strip()]
                self._send_json(HTTPStatus.OK, STATE.replace_option_watchlist(symbols))
                return
            if parsed.path == "/api/mag7-scanner-watchlist":
                if "ticker" in body:
                    symbol = str(body.get("ticker", "")).strip().upper()
                    if not symbol:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "ticker is required."})
                        return
                    self._send_json(HTTPStatus.CREATED, STATE.add_mag7_scanner_symbol(symbol))
                    return
                symbols = body.get("symbols", [])
                if isinstance(symbols, str):
                    symbols = [item.strip().upper() for item in symbols.replace("\n", ",").split(",") if item.strip()]
                self._send_json(HTTPStatus.OK, STATE.replace_mag7_scanner_watchlist(symbols))
                return
            if parsed.path == "/api/watchlist/add":
                symbol = str(body.get("symbol", body.get("ticker", ""))).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.CREATED, STATE.add_watchlist_symbol(symbol))
                return
            if parsed.path == "/api/option-watchlist/add":
                symbol = str(body.get("symbol", body.get("ticker", ""))).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.CREATED, STATE.add_option_watchlist_symbol(symbol))
                return
            if parsed.path == "/api/mag7-scanner-watchlist/add":
                symbol = str(body.get("symbol", body.get("ticker", ""))).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.CREATED, STATE.add_mag7_scanner_symbol(symbol))
                return
            if parsed.path == "/api/watchlist/delete":
                symbol = str(body.get("symbol", "")).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_watchlist_symbol(symbol))
                return
            if parsed.path == "/api/option-watchlist/delete":
                symbol = str(body.get("symbol", "")).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_option_watchlist_symbol(symbol))
                return
            if parsed.path == "/api/mag7-scanner-watchlist/delete":
                symbol = str(body.get("symbol", "")).strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_mag7_scanner_symbol(symbol))
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {parsed.path}"})
        except AuthorizationError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except AuthenticationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_PUT(self) -> None:
        try:
            parsed = urlparse(self.path)
            if not self._api_gate_passed(parsed.path):
                return
            body = self._read_json_body()
            if parsed.path.startswith("/api/watchlist/"):
                current = parsed.path.rsplit("/", 1)[-1].strip().upper()
                replacement = str(body.get("ticker", body.get("symbol", ""))).strip().upper()
                if not current:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Current ticker is required."})
                    return
                if not replacement:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Replacement ticker is required."})
                    return
                updated = [replacement if str(item).upper() == current else str(item).upper() for item in settings.scanner.default_universe]
                if current not in [str(item).upper() for item in settings.scanner.default_universe]:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ticker not found."})
                    return
                self._send_json(HTTPStatus.OK, STATE.replace_watchlist(updated))
                return
            if parsed.path.startswith("/api/mag7-scanner-watchlist/"):
                current = parsed.path.rsplit("/", 1)[-1].strip().upper()
                replacement = str(body.get("ticker", body.get("symbol", ""))).strip().upper()
                if not current:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Current ticker is required."})
                    return
                if not replacement:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Replacement ticker is required."})
                    return
                updated = [replacement if str(item).upper() == current else str(item).upper() for item in STATE.mag7_scanner_watchlist]
                if current not in [str(item).upper() for item in STATE.mag7_scanner_watchlist]:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ticker not found."})
                    return
                self._send_json(HTTPStatus.OK, STATE.replace_mag7_scanner_watchlist(updated))
                return
            if parsed.path.startswith("/api/option-watchlist/"):
                current = STATE._normalize_option_symbol(parsed.path.rsplit("/", 1)[-1])
                replacement = STATE._normalize_option_symbol(body.get("ticker", body.get("symbol", "")))
                if not current:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Current ticker is required."})
                    return
                if not replacement:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Replacement ticker is required."})
                    return
                updated = [replacement if str(item).upper() == current else str(item).upper() for item in STATE.option_watchlist]
                if current not in [str(item).upper() for item in STATE.option_watchlist]:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Ticker not found."})
                    return
                self._send_json(HTTPStatus.OK, STATE.replace_option_watchlist(updated))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {parsed.path}"})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_DELETE(self) -> None:
        try:
            parsed = urlparse(self.path)
            if not self._api_gate_passed(parsed.path):
                return
            if parsed.path.startswith("/api/watchlist/"):
                symbol = parsed.path.rsplit("/", 1)[-1].strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Ticker is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_watchlist_symbol(symbol))
                return
            if parsed.path.startswith("/api/mag7-scanner-watchlist/"):
                symbol = parsed.path.rsplit("/", 1)[-1].strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Ticker is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_mag7_scanner_symbol(symbol))
                return
            if parsed.path.startswith("/api/option-watchlist/"):
                symbol = parsed.path.rsplit("/", 1)[-1].strip().upper()
                if not symbol:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Ticker is required."})
                    return
                self._send_json(HTTPStatus.OK, STATE.remove_option_watchlist_symbol(symbol))
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown route: {parsed.path}"})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        response = json.dumps(_serialize_value(payload)).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(response)


QUICK_STRIP_WARM_SYMBOLS = (
    "SPY", "QQQ", "SLV", "AAPL", "AMZN", "GOOGL", "META",
    "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO", "PLTR",
)


def main() -> None:
    # Chart history is demand-driven. Eagerly rebuilding every quick-strip
    # ticker used to keep one CPU core and the broker busy for minutes after a
    # restart, exactly when traders need the first chart/option panel fastest.
    # Persistent caches make selected symbols instant without that competition.
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f"API server listening on http://{HOST}:{PORT}")
    auto_login_email = str(os.getenv("LOCAL_AUTO_LOGIN_EMAIL", "") or "").strip()
    if auto_login_email:
        print(
            f"WARNING: sign-in is disabled for loopback requests. Every local "
            f"request runs as {auto_login_email}. Clear LOCAL_AUTO_LOGIN_EMAIL "
            f"in .env to restore it."
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
