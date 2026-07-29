import os
from dataclasses import dataclass, field
from pathlib import Path
import re

from dotenv import load_dotenv

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "trades.db"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
TRAINING_DIR = BASE_DIR / "training"
WATCHLIST_PATH = Path(os.getenv("WATCHLIST_FILE", BASE_DIR / "watchlist.txt"))
EASTERN_TZ = "America/New_York"


def _csv_symbols(raw: str) -> list[str]:
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _slug_token(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(raw or "").strip().upper()).strip("_")


def _auto_profile_tokens() -> list[str]:
    tokens: set[str] = set()
    pattern = re.compile(r"^ALPACA_PROFILE_([A-Z0-9_]+)_KEY_ID$")
    for key in os.environ:
        match = pattern.match(key)
        if match:
            tokens.add(match.group(1))
    return sorted(tokens)


def _load_watchlist() -> list[str]:
    if WATCHLIST_PATH.exists():
        symbols: list[str] = []
        seen: set[str] = set()
        for token in WATCHLIST_PATH.read_text(encoding="utf-8").replace(",", " ").split():
            symbol = token.strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
        if symbols:
            return symbols
    return _csv_symbols(
        os.getenv(
            "SCANNER_UNIVERSE",
            "AAPL,AMD,AMZN,AVGO,COIN,CRM,CRWD,DKNG,GOOG,GS,HOOD,INTC,"
            "IWM,JPM,MARA,META,MSFT,MSTR,NFLX,NIO,NVDA,ORCL,PLTR,QQQ,"
            "RBLX,RIOT,ROKU,SHOP,SMCI,SNOW,SOFI,SPOT,TSLA,UBER,UNH",
        )
    )


@dataclass(slots=True)
class ScannerSettings:
    min_price: float = float(os.getenv("MIN_PRICE", "3"))
    min_avg_volume: int = int(os.getenv("MIN_AVG_VOLUME", "1000000"))
    min_rvol: float = float(os.getenv("MIN_RVOL", "2.5"))
    max_results: int = int(os.getenv("SCANNER_MAX_RESULTS", "15"))
    lookback_daily_bars: int = int(os.getenv("SCANNER_DAILY_LOOKBACK", "60"))
    intraday_timeframe: str = os.getenv("SCANNER_INTRADAY_TIMEFRAME", "5Min")
    intraday_signal_lookback_days: int = int(os.getenv("SCANNER_INTRADAY_SIGNAL_LOOKBACK_DAYS", "5"))
    signal_lookback_bars: int = int(os.getenv("SCANNER_SIGNAL_LOOKBACK_BARS", "2"))
    min_one_hour_close_change_pct: float = float(os.getenv("MIN_1H_CLOSE_CHANGE_PCT", "0.3"))
    min_four_hour_price_change_pct: float = float(os.getenv("MIN_4H_PRICE_CHANGE_PCT", "0.5"))
    min_four_hour_volume_change_pct: float = float(os.getenv("MIN_4H_VOLUME_CHANGE_PCT", "0.5"))
    signal_include_extended_hours: bool = os.getenv("SCANNER_SIGNAL_INCLUDE_EXTENDED_HOURS", "true").lower() == "true"
    loop_seconds: int = int(os.getenv("SCANNER_LOOP_SECONDS", "60"))
    stock_scanner_auto_interval_seconds: int = int(os.getenv("STOCK_SCANNER_AUTO_INTERVAL_SECONDS", "60"))
    stock_scanner_deep_batch_size: int = int(os.getenv("STOCK_SCANNER_DEEP_BATCH_SIZE", "12"))
    stock_scanner_hot_lane_size: int = int(os.getenv("STOCK_SCANNER_HOT_LANE_SIZE", "6"))
    oi_mag7_auto_interval_seconds: int = int(os.getenv("OI_MAG7_AUTO_INTERVAL_SECONDS", "15"))
    oi_mag7_continuous_pause_seconds: float = float(os.getenv("OI_MAG7_CONTINUOUS_PAUSE_SECONDS", "1"))
    oi_watchlist_auto_interval_seconds: int = int(os.getenv("OI_WATCHLIST_AUTO_INTERVAL_SECONDS", "30"))
    oi_watchlist_batch_size: int = int(os.getenv("OI_WATCHLIST_BATCH_SIZE", "25"))
    oi_watchlist_worker_count: int = int(os.getenv("OI_WATCHLIST_WORKER_COUNT", "5"))
    oi_watchlist_continuous_pause_seconds: float = float(os.getenv("OI_WATCHLIST_CONTINUOUS_PAUSE_SECONDS", "1"))
    # OI Finder history is a separate after-close archival task.  It is not a
    # live scanner: one sequential, rate-limited chain request per symbol per
    # trading day creates the daily volume/OI comparisons used by the Finder.
    oi_finder_snapshot_enabled: bool = os.getenv("OI_FINDER_SNAPSHOT_ENABLED", "true").lower() == "true"
    oi_finder_snapshot_hour_et: int = int(os.getenv("OI_FINDER_SNAPSHOT_HOUR_ET", "16"))
    oi_finder_snapshot_minute_et: int = int(os.getenv("OI_FINDER_SNAPSHOT_MINUTE_ET", "15"))
    oi_finder_snapshot_interval_seconds: int = int(os.getenv("OI_FINDER_SNAPSHOT_INTERVAL_SECONDS", "15"))
    # The live volume card is collected automatically for the small saved
    # MAG7 scanner list only.  The larger saved watchlist stays on-demand:
    # its option chain is requested only when the trader searches that ticker.
    oi_finder_mag7_live_enabled: bool = os.getenv("OI_FINDER_MAG7_LIVE_ENABLED", "true").lower() == "true"
    oi_finder_mag7_live_interval_seconds: int = int(os.getenv("OI_FINDER_MAG7_LIVE_INTERVAL_SECONDS", "120"))
    oi_finder_mag7_live_symbol_pause_seconds: float = float(os.getenv("OI_FINDER_MAG7_LIVE_SYMBOL_PAUSE_SECONDS", "8"))
    # Two sampled bars means one comparison: current volume > previous volume.
    volume_acceleration_bars: int = int(os.getenv("VOLUME_ACCELERATION_BARS", "2"))
    score_threshold: int = int(os.getenv("SCORE_THRESHOLD", "60"))
    min_intraday_change_pct: float = float(os.getenv("MIN_INTRADAY_CHANGE_PCT", "0.5"))
    min_session_change_pct: float = float(os.getenv("MIN_SESSION_CHANGE_PCT", "1.0"))
    fast_quote_prefilter_enabled: bool = os.getenv("SCANNER_FAST_QUOTE_PREFILTER_ENABLED", "true").lower() == "true"
    fast_quote_prefilter_max_symbols: int = int(os.getenv("SCANNER_FAST_QUOTE_PREFILTER_MAX_SYMBOLS", "120"))
    fast_quote_prefilter_min_change_pct: float = float(
        os.getenv("SCANNER_FAST_QUOTE_PREFILTER_MIN_CHANGE_PCT", os.getenv("MIN_SESSION_CHANGE_PCT", "1.0"))
    )
    aggressive_morning_enabled: bool = os.getenv("AGGRESSIVE_MORNING_MODE_ENABLED", "true").lower() == "true"
    aggressive_morning_minutes: int = int(os.getenv("AGGRESSIVE_MORNING_MINUTES", "90"))
    aggressive_morning_min_rvol: float = float(os.getenv("AGGRESSIVE_MORNING_MIN_RVOL", "1.2"))
    max_extension_above_ema9_pct: float = float(os.getenv("MAX_EXTENSION_ABOVE_EMA9_PCT", "3.0"))
    ema9_retest_lookback_bars: int = int(os.getenv("EMA9_RETEST_LOOKBACK_BARS", "3"))
    max_close_off_high_pct: float = float(os.getenv("MAX_CLOSE_OFF_HIGH_PCT", "25.0"))
    tos_rvol_length: int = int(os.getenv("TOS_RVOL_LENGTH", "50"))
    tos_rvol_num_dev: float = float(os.getenv("TOS_RVOL_NUM_DEV", "1.0"))
    tos_rvol_mag7_num_dev: float = float(os.getenv("TOS_RVOL_MAG7_NUM_DEV", "0.4"))
    tos_rvol_mid_timeframe_num_dev: float = float(os.getenv("TOS_RVOL_MID_TIMEFRAME_NUM_DEV", "1.0"))
    tos_rvol_five_min_early_num_dev: float = float(os.getenv("TOS_RVOL_5M_EARLY_NUM_DEV", "2.0"))
    tos_rvol_gate_enabled: bool = os.getenv("TOS_RVOL_GATE_ENABLED", "true").lower() == "true"
    history_retention_days: int = int(os.getenv("SCANNER_HISTORY_RETENTION_DAYS", "60"))
    default_universe: list[str] = field(default_factory=_load_watchlist)


@dataclass(slots=True)
class TradingSettings:
    journal_retention_days: int = int(os.getenv("TRADE_JOURNAL_RETENTION_DAYS", "183"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))
    daily_trade_amount: float = float(os.getenv("DAILY_TRADE_AMOUNT", "5000"))
    fixed_trade_amount: float = float(os.getenv("FIXED_TRADE_AMOUNT", "500"))
    stop_loss_percent: float = float(os.getenv("STOP_LOSS_PERCENT", "2.0"))
    stop_loss_amount: float = float(os.getenv("STOP_LOSS_AMOUNT", "100"))
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "0"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
    enforce_daily_loss_limit: bool = os.getenv("ENFORCE_DAILY_LOSS_LIMIT", "false").lower() == "true"
    reward_to_risk: float = float(os.getenv("REWARD_TO_RISK", "2.0"))
    trade_size_fraction: float = float(os.getenv("TRADE_SIZE_FRACTION", "0.10"))
    take_profit_1_pct: float = float(os.getenv("TAKE_PROFIT_1_PCT", "5.0"))
    take_profit_1_sell_pct: float = float(os.getenv("TAKE_PROFIT_1_SELL_PCT", "80.0"))
    runner_target_pct: float = float(os.getenv("RUNNER_TARGET_PCT", "10.0"))
    runner_stop_buffer_pct: float = float(os.getenv("RUNNER_STOP_BUFFER_PCT", "1.0"))
    trade_start_after_et: str = os.getenv("TRADE_START_AFTER_ET", "09:35")
    no_new_trades_after_et: str = os.getenv("NO_NEW_TRADES_AFTER_ET", "15:30")
    default_trade_side: str = os.getenv("DEFAULT_TRADE_SIDE", "buy")
    allow_fractional_shares: bool = os.getenv("ALLOW_FRACTIONAL_SHARES", "false").lower() == "true"
    trade_all_sessions: bool = os.getenv("TRADE_ALL_SESSIONS", "true").lower() == "true"
    min_rule_score: int = int(os.getenv("MIN_RULE_SCORE", "70"))
    auto_trade_score_threshold: int = int(os.getenv("AUTO_TRADE_SCORE_THRESHOLD", "50"))
    stock_auto_oi_confirmation_max_age_seconds: int = int(os.getenv("STOCK_AUTO_OI_CONFIRMATION_MAX_AGE_SECONDS", "300"))
    option_auto_oi_confirmation_max_age_seconds: int = int(os.getenv("OPTION_AUTO_OI_CONFIRMATION_MAX_AGE_SECONDS", "45"))
    excluded_symbols: list[str] = field(default_factory=lambda: _csv_symbols(os.getenv(
        "EXCLUDED_TRADE_SYMBOLS",
        "SPY,QQQ",
    )))


@dataclass(slots=True)
class BacktestSettings:
    bars_to_fetch: int = int(os.getenv("BACKTEST_BARS_TO_FETCH", "1260"))
    breakout_lookback: int = int(os.getenv("BACKTEST_BREAKOUT_LOOKBACK", "20"))
    max_hold_days: int = int(os.getenv("BACKTEST_MAX_HOLD_DAYS", "5"))
    initial_capital: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "100000"))


@dataclass(slots=True)
class AISettings:
    enabled: bool = os.getenv("AI_ENABLED", "true").lower() == "true"
    min_trade_score: int = int(os.getenv("AI_MIN_TRADE_SCORE", "50"))
    model_min_win_probability: float = float(os.getenv("AI_MODEL_MIN_WIN_PROBABILITY", "0.58"))
    model_min_expected_r: float = float(os.getenv("AI_MODEL_MIN_EXPECTED_R", "0.15"))
    model_edge_weight: float = float(os.getenv("AI_MODEL_EDGE_WEIGHT", "0.55"))
    heuristic_weight: float = float(os.getenv("AI_HEURISTIC_WEIGHT", "0.45"))
    llm_reasoning_enabled: bool = os.getenv("AI_LLM_REASONING_ENABLED", "true").lower() == "true"
    llm_agent_enabled: bool = os.getenv("LLM_AGENT_ENABLED", "true").lower() == "true"
    llm_agent_external_enabled: bool = os.getenv("LLM_AGENT_EXTERNAL_ENABLED", "false").lower() == "true"
    llm_agent_provider: str = os.getenv("LLM_AGENT_PROVIDER", "openai").lower()
    llm_agent_model: str = os.getenv("LLM_AGENT_MODEL", "gpt-4o-mini")
    llm_agent_max_candidates: int = int(os.getenv("LLM_AGENT_MAX_CANDIDATES", "5"))
    llm_agent_timeout_seconds: float = float(os.getenv("LLM_AGENT_TIMEOUT_SECONDS", "1.5"))
    predictive_model_path: str = os.getenv("AI_PREDICTIVE_MODEL_PATH", str(ARTIFACTS_DIR / "trade_model.json"))
    ml_weight: float = float(os.getenv("AI_ML_WEIGHT", "0.35"))
    # Retained for configuration compatibility. Catalyst/news is information-only
    # and is deliberately excluded from execution scores.
    catalyst_weight: float = float(os.getenv("AI_CATALYST_WEIGHT", "0.0"))
    regime_weight: float = float(os.getenv("AI_REGIME_WEIGHT", "0.15"))
    memory_weight: float = float(os.getenv("AI_MEMORY_WEIGHT", "0.15"))
    anomaly_weight: float = float(os.getenv("AI_ANOMALY_WEIGHT", "0.15"))
    option_llm_supervisor_enabled: bool = os.getenv("OPTION_LLM_SUPERVISOR_ENABLED", "true").lower() == "true"
    option_llm_supervisor_model: str = os.getenv("OPTION_LLM_SUPERVISOR_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    option_llm_supervisor_timeout_seconds: float = float(os.getenv("OPTION_LLM_SUPERVISOR_TIMEOUT_SECONDS", "2.5"))


@dataclass(slots=True)
class BrokerCredentials:
    profile_id: str
    label: str
    key: str
    secret: str
    paper: bool


@dataclass(slots=True)
class SchwabSettings:
    client_id: str = os.getenv("SCHWAB_CLIENT_ID", "")
    client_secret: str = os.getenv("SCHWAB_CLIENT_SECRET", "")
    redirect_uri: str = os.getenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1/")
    refresh_token: str = os.getenv("SCHWAB_REFRESH_TOKEN", "")
    access_token: str = os.getenv("SCHWAB_ACCESS_TOKEN", "")
    token_path: str = os.getenv("SCHWAB_TOKEN_PATH", str(ARTIFACTS_DIR / "schwab_token.json"))
    include_extended_hours: bool = os.getenv("SCHWAB_EXTENDED_HOURS", "true").lower() == "true"
    timeout_seconds: int = int(os.getenv("SCHWAB_TIMEOUT_SECONDS", "15"))


@dataclass(slots=True)
class SchwabTradingSettings:
    client_id: str = os.getenv("SCHWAB_TRADING_CLIENT_ID", "")
    client_secret: str = os.getenv("SCHWAB_TRADING_CLIENT_SECRET", "")
    redirect_uri: str = os.getenv("SCHWAB_TRADING_REDIRECT_URI", os.getenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1/"))
    refresh_token: str = ""
    access_token: str = ""
    token_path: str = os.getenv("SCHWAB_TRADING_TOKEN_PATH", str(ARTIFACTS_DIR / "schwab_trading_token.json"))
    include_extended_hours: bool = os.getenv("SCHWAB_EXTENDED_HOURS", "true").lower() == "true"
    timeout_seconds: int = int(os.getenv("SCHWAB_TIMEOUT_SECONDS", "15"))


@dataclass(slots=True)
class TradierSettings:
    access_token: str = os.getenv("TRADIER_ACCESS_TOKEN", "")
    base_url: str = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1").rstrip("/")
    timeout_seconds: int = int(os.getenv("TRADIER_TIMEOUT_SECONDS", "15"))


@dataclass(slots=True)
class AppConfig:
    execution_mode: str = os.getenv("EXECUTION_MODE", "paper").lower()
    allow_live_trading: bool = os.getenv("ALLOW_LIVE_TRADING", "false").lower() == "true"
    auto_start_bot: bool = os.getenv("AUTO_START_BOT", "true").lower() == "true"
    market_data_provider: str = os.getenv("MARKET_DATA_PROVIDER", "alpaca").lower()
    alpaca_data_feed: str = os.getenv("ALPACA_DATA_FEED", "iex").lower()
    alpaca_streaming_enabled: bool = os.getenv("ALPACA_STREAMING_ENABLED", "true").lower() == "true"
    alpaca_trade_updates_enabled: bool = os.getenv("ALPACA_TRADE_UPDATES_ENABLED", "true").lower() == "true"
    runtime_watchdog_enabled: bool = os.getenv("RUNTIME_WATCHDOG_ENABLED", "true").lower() == "true"
    runtime_watchdog_auto_recover: bool = os.getenv("RUNTIME_WATCHDOG_AUTO_RECOVER", "true").lower() == "true"
    runtime_watchdog_interval_seconds: int = int(os.getenv("RUNTIME_WATCHDOG_INTERVAL_SECONDS", "15"))
    runtime_watchdog_stale_multiplier: float = float(os.getenv("RUNTIME_WATCHDOG_STALE_MULTIPLIER", "4.0"))
    schwab: SchwabSettings = field(default_factory=SchwabSettings)
    schwab_trading: SchwabTradingSettings = field(default_factory=SchwabTradingSettings)
    tradier: TradierSettings = field(default_factory=TradierSettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    trading: TradingSettings = field(default_factory=TradingSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)
    ai: AISettings = field(default_factory=AISettings)
    default_account_profile: str = os.getenv("ACTIVE_ALPACA_PROFILE", "paper4").lower()
    mag7_option_account_profile: str = (
        os.getenv("OPTION_ACCOUNT_PROFILE")
        or os.getenv("OPTION_ALPACA_PROFILE")
        or "paper3"
    ).lower()
    watchlist_option_account_profile: str = os.getenv("WATCHLIST_OPTION_ACCOUNT_PROFILE", "paper5").lower()
    stock_account_label: str = os.getenv("OPTION_ACCOUNT_LABEL4", "Watchlist Stock trade")
    mag7_option_account_label: str = os.getenv(
        "OPTION_ACCOUNT_LABEL3",
        os.getenv("OPTION_ACCOUNT_LABEL", "Mag7 OPTION TRADE"),
    )
    watchlist_option_account_label: str = os.getenv("OPTION_ACCOUNT_LABEL5", "Watchlist OPTION TRADE")

    @property
    def missing_credentials(self) -> bool:
        credentials = self.credentials_for_profile(self.default_account_profile, self.execution_mode)
        return not credentials.key or not credentials.secret

    @property
    def is_paper_mode(self) -> bool:
        return self.execution_mode != "live"

    @property
    def account_label(self) -> str:
        return "Paper" if self.is_paper_mode else "Live"

    def available_profiles(self, mode: str | None = None) -> list[BrokerCredentials]:
        selected_mode = (mode or self.execution_mode).lower()
        profiles: list[BrokerCredentials] = []
        seen_ids: set[str] = set()

        configured_tokens: list[tuple[str, str]] = []
        explicit_profile_names = _csv_symbols(os.getenv("ALPACA_ACCOUNT_PROFILES", ""))
        for raw_name in explicit_profile_names:
            configured_tokens.append((raw_name.lower(), _slug_token(raw_name)))
        if not explicit_profile_names:
            default_credentials = self._default_credentials(selected_mode)
            if default_credentials.key and default_credentials.secret:
                profiles.append(default_credentials)
                seen_ids.add(default_credentials.profile_id.lower())
            for token in _auto_profile_tokens():
                configured_tokens.append((token.lower(), token))

        for profile_id, token in configured_tokens:
            key = os.getenv(f"ALPACA_PROFILE_{token}_KEY_ID", "")
            secret = os.getenv(f"ALPACA_PROFILE_{token}_SECRET_KEY", "")
            if not key or not secret:
                continue
            paper = os.getenv(f"ALPACA_PROFILE_{token}_PAPER", "true").lower() != "false"
            if selected_mode == "paper" and not paper:
                continue
            if selected_mode == "live" and paper:
                continue
            if profile_id in seen_ids:
                continue
            profiles.append(
                BrokerCredentials(
                    profile_id=profile_id,
                    label=os.getenv(f"ALPACA_PROFILE_{token}_LABEL", token.replace("_", " ").title()),
                    key=key,
                    secret=secret,
                    paper=paper,
                )
            )
            seen_ids.add(profile_id)
        return profiles

    def credentials_for_profile(self, profile_id: str | None = None, mode: str | None = None) -> BrokerCredentials:
        selected_mode = (mode or self.execution_mode).lower()
        requested = (profile_id or self.default_account_profile or "default-paper").lower()
        for profile in self.available_profiles(selected_mode):
            if profile.profile_id.lower() == requested:
                return profile
        if os.getenv("ALPACA_ACCOUNT_PROFILES", "").strip():
            return BrokerCredentials(
                profile_id=requested,
                label=requested,
                key="",
                secret="",
                paper=selected_mode != "live",
            )
        return self._default_credentials(selected_mode)

    def trade_label_for_profile(self, profile_id: str | None) -> str:
        requested = str(profile_id or "").strip().lower()
        if requested == "paper4":
            return self.stock_account_label
        if requested == "paper3":
            return self.mag7_option_account_label
        if requested == "paper5":
            return self.watchlist_option_account_label
        return requested or self.account_label

    def credentials_for_mode(self, mode: str | None = None) -> BrokerCredentials:
        selected_mode = (mode or self.execution_mode).lower()
        return self._default_credentials(selected_mode)

    def option_account_profile_id(self, mode: str | None = None) -> str:
        selected_mode = (mode or "paper").lower()
        explicit = (self.mag7_option_account_profile or "").strip().lower()
        if explicit:
            return explicit

        profiles = self.available_profiles(selected_mode)
        target_label = (self.mag7_option_account_label or "Mag7 OPTION TRADE").strip().upper()
        for profile in profiles:
            if profile.label.strip().upper() == target_label:
                return profile.profile_id.lower()
        for profile in profiles:
            if profile.profile_id.lower() == "paper3":
                return profile.profile_id.lower()
        return (self.default_account_profile or "default-paper").lower()

    def watchlist_option_account_profile_id(self, mode: str | None = None) -> str:
        requested = (self.watchlist_option_account_profile or "paper5").strip().lower()
        available = {profile.profile_id.lower() for profile in self.available_profiles(mode or "paper")}
        return requested if requested in available else "paper5"

    def option_account_profile_ids(self, mode: str | None = None) -> set[str]:
        return {
            self.option_account_profile_id(mode),
            self.watchlist_option_account_profile_id(mode),
        }

    def is_option_account_profile(self, profile_id: str | None, mode: str | None = None) -> bool:
        requested = str(profile_id or "").strip().lower()
        if not requested:
            return False
        return requested in self.option_account_profile_ids(mode)

    def stock_account_profile_id(self, mode: str | None = None) -> str:
        selected_mode = (mode or self.execution_mode).lower()
        requested = (self.default_account_profile or "default-paper").lower()
        if requested and not self.is_option_account_profile(requested, selected_mode):
            return requested
        for profile in self.available_profiles(selected_mode):
            if not self.is_option_account_profile(profile.profile_id, selected_mode):
                return profile.profile_id.lower()
        return requested or "default-paper"

    def _default_credentials(self, selected_mode: str) -> BrokerCredentials:
        if selected_mode == "live":
            return BrokerCredentials(
                profile_id="default-live",
                label="Default Live",
                key=os.getenv("ALPACA_LIVE_KEY_ID") or os.getenv("ALPACA_LIVE_API_KEY", ""),
                secret=os.getenv("ALPACA_LIVE_SECRET_KEY") or os.getenv("ALPACA_LIVE_API_SECRET", ""),
                paper=False,
            )
        return BrokerCredentials(
            profile_id="default-paper",
            label=os.getenv("ALPACA_DEFAULT_PAPER_LABEL", "Default Paper"),
            key=os.getenv("ALPACA_PAPER_KEY_ID")
            or os.getenv("ALPACA_API_KEY")
            or os.getenv("ALPACA_KEY_ID", ""),
            secret=os.getenv("ALPACA_PAPER_SECRET_KEY")
            or os.getenv("ALPACA_API_SECRET")
            or os.getenv("ALPACA_SECRET_KEY", ""),
            paper=True,
        )


settings = AppConfig()
