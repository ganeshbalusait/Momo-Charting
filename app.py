from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from backtester import Backtester
from config import DATABASE_PATH, settings
from data.alpaca_client import AlpacaClient
from data.market_data import create_market_data_client
from database.repository import TradingRepository
from execution.alpaca_paper_trader import AlpacaPaperTrader
from scanner import MomentumScanner


st.set_page_config(page_title="AI Momentum Trading System", page_icon=":chart_with_upwards_trend:", layout="wide")
SCAN_RESULTS_SCHEMA_VERSION = 2


def _format_clock(value: datetime | None) -> str:
    if value is None:
        return "Unavailable"
    return value.astimezone().strftime("%Y-%m-%d %I:%M:%S %p %Z")


@st.cache_resource(show_spinner=False)
def get_client() -> AlpacaClient:
    return AlpacaClient()


@st.cache_resource(show_spinner=False)
def get_market_data_client():
    return create_market_data_client(get_client())


@st.cache_resource(show_spinner=False)
def get_repository() -> TradingRepository:
    return TradingRepository()


@st.cache_resource(show_spinner=False)
def get_scanner() -> MomentumScanner:
    return MomentumScanner(client=get_market_data_client())


@st.cache_resource(show_spinner=False)
def get_trader() -> AlpacaPaperTrader:
    return AlpacaPaperTrader(client=get_client(), scanner=get_scanner(), repository=get_repository())


@st.cache_resource(show_spinner=False)
def get_backtester() -> Backtester:
    return Backtester(client=get_market_data_client())


def _initialize_state() -> None:
    if st.session_state.get("scan_results_schema_version") != SCAN_RESULTS_SCHEMA_VERSION:
        st.session_state["scan_results"] = pd.DataFrame()
        st.session_state["candidate_results"] = pd.DataFrame()
        st.session_state["scan_timestamp"] = None
        st.session_state["scan_results_schema_version"] = SCAN_RESULTS_SCHEMA_VERSION
    st.session_state.setdefault("bot_state", "Stopped")
    st.session_state.setdefault("scan_results", pd.DataFrame())
    st.session_state.setdefault("candidate_results", pd.DataFrame())
    st.session_state.setdefault("scan_timestamp", None)
    st.session_state.setdefault("backtest_summary", pd.DataFrame())
    st.session_state.setdefault("backtest_trades", pd.DataFrame())
    st.session_state.setdefault("action_message", "")


def _available_columns(frame: pd.DataFrame, requested: list[str]) -> list[str]:
    return [column for column in requested if column in frame.columns]


def _bot_controls(trader: AlpacaPaperTrader) -> None:
    st.sidebar.header(f"{settings.account_label} Bot Controls")
    start, pause, stop = st.sidebar.columns(3)

    if start.button("Start"):
        st.session_state.bot_state = "Running"
        st.session_state.action_message = f"{settings.account_label} bot marked as running. Use 'Execute Best Trade' to submit an order."
    if pause.button("Pause"):
        st.session_state.bot_state = "Paused"
        st.session_state.action_message = "Paper bot paused."
    if stop.button("Stop"):
        st.session_state.bot_state = "Stopped"
        st.session_state.action_message = "Paper bot stopped."

    st.sidebar.metric("Bot State", st.session_state.bot_state)
    if st.sidebar.button("Sync Orders"):
        trader.sync_order_statuses()
        st.session_state.action_message = "Order status sync complete."


def _show_header(client: AlpacaClient, trader: AlpacaPaperTrader) -> None:
    clock = client.get_clock()
    status = trader.get_status()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Market Status", "Open" if clock.is_open else "Closed")
    col2.metric(f"{settings.account_label} Equity", f"${status.account_equity:,.2f}")
    col3.metric("Daily P/L", f"${status.daily_pnl:,.2f}")
    col4.metric("Trades Today", status.trades_today)

    sub1, sub2, sub3, sub4 = st.columns(4)
    sub1.metric("Clock Time", _format_clock(clock.timestamp))
    sub2.metric("Next Open", _format_clock(clock.next_open))
    sub3.metric("Next Close", _format_clock(clock.next_close))
    sub4.metric("Buying Power", f"${status.buying_power:,.2f}")


def _run_scan(trader: AlpacaPaperTrader) -> None:
    with st.spinner("Scanning Alpaca market data..."):
        scan_results, candidate_results = trader.run_scan_and_prepare_trades()
        st.session_state.scan_results = scan_results
        st.session_state.candidate_results = candidate_results
        st.session_state.scan_timestamp = datetime.now().astimezone()


def _show_scanner_panel(trader: AlpacaPaperTrader) -> None:
    st.subheader("Momentum Scanner")
    left, right = st.columns([2, 1])
    if left.button("Refresh Scanner", type="primary"):
        _run_scan(trader)
    if right.button("Execute Best Trade"):
        response = trader.execute_best_candidate()
        st.session_state.action_message = response["message"]
        _run_scan(trader)

    if st.session_state.action_message:
        st.info(st.session_state.action_message)

    results = st.session_state.scan_results
    candidates = st.session_state.candidate_results
    st.caption(f"Last refresh: {_format_clock(st.session_state.scan_timestamp)}")

    if results.empty:
        st.info("No symbols matched the momentum scan yet. Try refreshing during active market hours.")
        return

    st.dataframe(
        results[
            _available_columns(
                results,
                [
                    "symbol",
                    "strategy_name",
                    "score",
                    "last_price",
                    "rvol",
                    "average_volume",
                    "today_volume",
                    "intraday_change_pct",
                    "extension_above_ema9_pct",
                    "trigger_source",
                    "opening_range_high",
                    "trigger_level",
                    "atr_value",
                    "entry",
                    "stop_loss",
                    "target",
                ],
            )
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Trade Candidates")
    if candidates.empty:
        st.warning("No trade candidates were generated from the current scan.")
    else:
        st.dataframe(
            candidates[
                _available_columns(
                    candidates,
                    [
                        "symbol",
                        "score",
                        "entry",
                        "stop_loss",
                        "target",
                        "risk_per_share",
                        "allowed",
                        "rejection_reason",
                        "rationale",
                    ],
                )
            ],
            use_container_width=True,
            hide_index=True,
        )


def _show_positions_and_history(trader: AlpacaPaperTrader, repository: TradingRepository) -> None:
    positions_col, history_col = st.columns(2)
    positions_col.subheader(f"{settings.account_label} Open Positions")
    positions = trader.fetch_open_positions_frame()
    if positions.empty:
        positions_col.info(f"No open {settings.account_label.lower()} positions.")
    else:
        positions_col.dataframe(positions, use_container_width=True, hide_index=True)

    history_col.subheader(f"{settings.account_label} Trade Journal")
    history = repository.get_trade_history()
    if history.empty:
        history_col.info("No trades recorded yet.")
    else:
        history_col.dataframe(history, use_container_width=True, hide_index=True)


def _show_backtest_panel(backtester: Backtester, repository: TradingRepository) -> None:
    st.subheader("Backtesting")
    default_symbols = ",".join(settings.scanner.default_universe[:5])
    symbols_text = st.text_input("Backtest symbols", value=default_symbols)

    if st.button("Run Backtest"):
        symbols = [item.strip().upper() for item in symbols_text.split(",") if item.strip()]
        with st.spinner("Running historical backtest..."):
            summary, trades = backtester.run(symbols)
            st.session_state.backtest_summary = summary
            st.session_state.backtest_trades = trades
            repository.log_backtest_run(symbols, summary)

    summary = st.session_state.backtest_summary
    trades = st.session_state.backtest_trades
    recent = repository.get_recent_backtests()

    if summary.empty:
        st.info("No backtest results yet.")
    else:
        stat1, stat2, stat3 = st.columns(3)
        stat1.metric("Total Trades", int(summary["trades"].sum()))
        stat2.metric("Avg Win Rate", f"{summary['win_rate'].mean():.2f}%")
        stat3.metric("Total P/L", f"${summary['total_pnl'].sum():,.2f}")

        range1, range2 = st.columns(2)
        if not trades.empty and "entry_date" in trades.columns:
            range1.metric("First Trade Date", str(trades["entry_date"].min()))
            range2.metric("Last Trade Date", str(trades["entry_date"].max()))

        if "data_start" in summary.columns and "data_end" in summary.columns:
            data1, data2 = st.columns(2)
            data1.metric("Data Start", str(summary["data_start"].min()))
            data2.metric("Data End", str(summary["data_end"].max()))

        st.dataframe(summary, use_container_width=True, hide_index=True)

    if not trades.empty:
        st.subheader("Backtest Trade Log")
        st.dataframe(trades, use_container_width=True, hide_index=True)

    st.subheader("Recent Backtest Runs")
    if recent.empty:
        st.info("No backtest history recorded yet.")
    else:
        st.dataframe(recent, use_container_width=True, hide_index=True)


def main() -> None:
    _initialize_state()

    st.title("AI Momentum Trading System")
    st.caption("Phase 2: scanner, strategy rules, backtesting, and Alpaca paper/live account separation")

    if settings.missing_credentials:
        st.error(f"Set the Alpaca {settings.account_label.lower()} credentials in .env before using the scanner, backtester, or trader.")
        st.stop()

    client = get_client()
    repository = get_repository()
    trader = get_trader()
    backtester = get_backtester()

    _bot_controls(trader)
    _show_header(client, trader)

    st.sidebar.header("Scanner Universe")
    st.sidebar.metric("Execution Mode", settings.account_label)
    if not settings.is_paper_mode and not settings.allow_live_trading:
        st.sidebar.warning("Live credentials can be loaded, but live order submission stays locked until ALLOW_LIVE_TRADING=true.")
    st.sidebar.write(", ".join(settings.scanner.default_universe))
    st.sidebar.caption(f"SQLite database: {DATABASE_PATH}")

    tab1, tab2, tab3 = st.tabs(["Scanner", "Paper Trading", "Backtesting"])
    with tab1:
        _show_scanner_panel(trader)
    with tab2:
        _show_positions_and_history(trader, repository)
    with tab3:
        _show_backtest_panel(backtester, repository)


if __name__ == "__main__":
    main()
