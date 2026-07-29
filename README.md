# AI Momentum Trading System

Phase 2 delivers an Alpaca paper-trading execution workflow, optional Alpaca or Schwab/TOS market data, a momentum scanner, a strategy and risk layer, an intraday backtester, and a dashboard experience.

## Current features

- Alpaca API connectivity with separate paper and live credential support
- Optional Schwab/TOS market-data adapter for scanner, chart, and backtest bars
- Paper-account profile selector for switching between locally configured Alpaca paper accounts
- Momentum scanner with score-based ranking
- Unified momentum/price-action setup with EMA, VWAP, ORB, and breakout confirmation
- Strategy engine that filters only high-conviction setups
- Risk manager enforcing 1 percent risk, 3 trades per day, and 2 percent max daily loss
- Intraday backtester aligned with the live setup rules
- Alpaca paper trading execution using bracket orders
- Alpaca stock WebSocket streaming for live market-data bars and quotes
- Alpaca trade-update streaming for paper/live account event visibility
- Streamlit dashboard for market status, scanner results, positions, trade journal, and backtests
- React + Vite + Tailwind frontend shell for a more polished scanner UI
- Lightweight Python API server that connects the React UI to Alpaca, SQLite, scanner, paper trading, and backtesting

## Strategy logic included in Phase 1

Core filters:

- Price greater than $20
- Average daily volume is tracked as a liquidity quality input, but increasing live volume is preferred over a hard daily-volume block
- Relative volume greater than or equal to 2.5
- Price above VWAP
- EMA 9 above EMA 21 above EMA 50
- Break above premarket high or previous day high
- Opening range breakout confirmation above the first 5-minute high
- First 5-minute candle bullish and closing above VWAP
- Signal bar must show at least 0.5 percent expansion
- Signal bar must close near the high
- Entry must not be more than 3 percent extended above EMA 9
- Increasing volume on 1-minute or 5-minute bars
- SPY above VWAP for market confirmation

Momentum score:

- RVOL: 20 points
- Volume acceleration: 20 points
- EMA trend: 15 points
- VWAP: 10 points
- Breakout: 15 points
- Market trend: 10 points
- News/catalyst placeholder: 10 points

Note: the catalyst score is still a placeholder and remains zero until a news source is integrated.

## Project structure

```text
AgenticAI-Trading/
|-- app.py
|-- backtester.py
|-- config.py
|-- indicators.py
|-- risk_manager.py
|-- scanner.py
|-- strategy.py
|-- data/
|   |-- alpaca_client.py
|   |-- market_data.py
|   `-- schwab_client.py
|-- database/
|   |-- repository.py
|   `-- trades.db
|-- execution/
|   `-- alpaca_paper_trader.py
```

## Setup

Use the same bootstrap flow on Windows, macOS, or Linux:

```bash
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
python scripts/bootstrap.py
```

The bootstrap script creates a local `.env` from `.env.example` when needed, provisions a virtual environment, installs Python dependencies, and installs frontend packages with either `pnpm` or `npm`.

Add your Alpaca paper and optional live API credentials in `.env` before launching the app:

```env
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
ALPACA_PAPER_KEY_ID=your_paper_key
ALPACA_PAPER_SECRET_KEY=your_paper_secret
ALPACA_DEFAULT_PAPER_LABEL=Main Paper
ALPACA_LIVE_KEY_ID=your_live_key
ALPACA_LIVE_SECRET_KEY=your_live_secret
ALPACA_ACCOUNT_PROFILES=paper2,paper3
ALPACA_PROFILE_PAPER2_LABEL=Second Paper
ALPACA_PROFILE_PAPER2_KEY_ID=your_second_paper_key
ALPACA_PROFILE_PAPER2_SECRET_KEY=your_second_paper_secret
ALPACA_PROFILE_PAPER3_LABEL=OPTION TRADE
ALPACA_PROFILE_PAPER3_KEY_ID=your_third_paper_key
ALPACA_PROFILE_PAPER3_SECRET_KEY=your_third_paper_secret
OPTION_ACCOUNT_PROFILE=paper3
OPTION_ACCOUNT_LABEL=OPTION TRADE
ACTIVE_ALPACA_PROFILE=default-paper
ALPACA_DATA_FEED=iex
ALPACA_STREAMING_ENABLED=true
ALPACA_TRADE_UPDATES_ENABLED=true
MARKET_DATA_PROVIDER=alpaca
SCHWAB_CLIENT_ID=your_schwab_app_key
SCHWAB_CLIENT_SECRET=your_schwab_app_secret
SCHWAB_REDIRECT_URI=https://127.0.0.1/
SCHWAB_REFRESH_TOKEN=
SCHWAB_EXTENDED_HOURS=true
SCANNER_UNIVERSE=AAPL,AMD,AMZN,AVGO,COIN,CRWD,META,MSFT,NVDA,PLTR,QQQ,SMCI,SOFI,TSLA,UBER
```

Compatibility note:
- Existing `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` values still work as paper credentials.
- Live mode requires `EXECUTION_MODE=live`.
- Live orders stay blocked unless `ALLOW_LIVE_TRADING=true`.
- Extra paper accounts are optional. Add them with `ALPACA_ACCOUNT_PROFILES` and `ALPACA_PROFILE_<NAME>_*` variables, then choose the active stock account in the UI.
- The `OPTION TRADE` profile is reserved for option paper trading. Stock trading is blocked on that account, and option journal records use `OPTION_ACCOUNT_PROFILE` separately from the active stock account.

Schwab/TOS data setup:
- Keep `EXECUTION_MODE=paper`; Alpaca remains the paper broker.
- Set `MARKET_DATA_PROVIDER=schwab` only when Schwab credentials are ready.
- Use `GET /api/schwab/auth-url` to generate the Schwab authorization link.
- After Schwab redirects back with a `code`, send it to `POST /api/schwab/token` as `{"code":"..."}` to cache the access and refresh token.
- Once connected, scanner, chart candles, and backtesting use Schwab price-history bars. Alpaca still handles account, orders, fills, positions, and paper journal execution.

Run the dashboard:

```powershell
streamlit run app.py
```

Run the React frontend with the Python API:

```powershell
python api_server.py
cd frontend
npm install
npm run dev
```

Frontend notes:

- The Vite dev server runs on `http://localhost:5173`.
- `vite.config.js` proxies `/api` requests to `http://localhost:3001`.
- `api_server.py` runs on `http://127.0.0.1:3001` and serves the real dashboard data.
- The React app includes Scanner, Paper Trading, and Backtesting tabs plus an agent-style decision pipeline inspired by RakshaQuant, adapted for the US market and Alpaca.

## Run continuously on Windows

The project includes two watchdog layers:

- `api_server.py` monitors and recovers required scanner, scheduler, position-manager, and learning threads.
- `scripts/scanner_watchdog.ps1` monitors the backend and dashboard processes and restarts a process after repeated failed health checks.
- `scripts/start_api_background.ps1` holds a machine-wide file lock while the backend runs, preventing duplicate supervised trading engines during slow restarts.

To start both layers automatically whenever your Windows account signs in, run:

```powershell
.\scripts\install_24x7_task.ps1
```

This creates the `AgenticAI-Trading-24x7` Windows Scheduled Task under your current account with limited privileges and starts it immediately. The task is configured to restart after a failure. After a reboot, it resumes when you sign in. The dashboard remains local at `http://127.0.0.1:5173`, and backend health is available at `http://127.0.0.1:3001/api/health`.

Keep the computer plugged in, connected to the internet, and configured not to sleep. This startup task does not enable live trading or change any execution, strategy, risk, account, or credential setting. After code or frontend dependency changes, restart the scheduled task so the supervised processes reload them.

To stop and remove automatic startup, run:

```powershell
.\scripts\uninstall_24x7_task.ps1
```

## Alpaca tools used

- `alpaca-py` is the primary SDK used by the app runtime.
- `Trading API` is used for account reads, positions, orders, and paper execution.
- `Market Data API` is used for historical bars and scanner inputs when `MARKET_DATA_PROVIDER=alpaca`.
- `WebSocket streaming` is used for live stock bars, quotes, and trade-update stream status.
- `Alpaca CLI` and `Alpaca MCP Server` remain optional developer tools. They are useful for manual testing or agent workflows, but they are not required for the trading app runtime.

## Schwab/TOS data mode

- Schwab/TOS is implemented as a read-only market-data adapter in `data/schwab_client.py`.
- It uses Schwab Trader API OAuth and the market-data price-history endpoint.
- It normalizes Schwab candles into the same `timestamp`, `open`, `high`, `low`, `close`, `volume`, `trade_count`, and `session_vwap` columns used by Alpaca data.
- It supports the existing app timeframes: `1Min`, `5Min`, and `1Day`.
- This mode does not place Schwab/TOS orders. Paper execution stays on Alpaca.

## Scanner notes

- The scanner uses `watchlist.txt` by default when that file exists.
- If `watchlist.txt` is missing, it falls back to `SCANNER_UNIVERSE` from `.env`.
- Alpaca IEX data is used by default to keep the setup simple. Set `MARKET_DATA_PROVIDER=schwab` to use Schwab/TOS bars for scanner inputs.
- Early-session RVOL is normalized by elapsed market-session time to avoid undercounting momentum shortly after the open.
- Average daily volume is no longer a hard scanner rejection; the bot now leans more on live volume acceleration and RVOL to confirm momentum.
- The strategy engine only allows entries with a score of at least 90 and blocks new trades after `11:00 AM ET`.
- The live scanner now uses one unified momentum/price-action long setup after `9:45 AM ET`.
- The rule set also applies pro-style breakout quality filters: expansion, close near high, and no chasing too far above EMA 9.
- Paper trades use Alpaca bracket orders with stop loss at 1 ATR and target at 2R.
- Trade journal, daily P/L, and open trades are separated by execution mode so paper and live records do not mix.

## Backtesting notes

- The backtester uses Alpaca intraday bars and the same unified momentum/price-action entry rules as the live scanner.
- Backtest results show the actual data span returned by Alpaca, which may be shorter than the requested lookback.
- It validates trend, breakout, and ATR-based exits across recent history for the chosen symbols.
- By default it requests about 5 years of lookback (`1260` trading days worth of calendar span). Override with `BACKTEST_BARS_TO_FETCH` in `.env` if you want a shorter or longer run.
- Because the live scanner uses intraday conditions and SPY VWAP confirmation, backtest results should be treated as directional validation rather than exact production-equivalent performance.

## Safe bilevel loop engineering

The repository includes an isolated Karpathy-style inner experiment loop plus an optional outer loop that changes the search strategy after repeated failures. It never edits or deploys the live checkout. Candidate edits happen under `artifacts/loop_engineering/<run-id>/workspace`, the immutable verifier decides keep/rollback, experiment state is appended to `experiments.jsonl`, and accepted work is exported as `champion.patch` for human review.

Run only the baseline verifier first:

```powershell
.venv\Scripts\python.exe scripts\run_loop_engineering.py --config loop_config.example.json --reset --baseline-only
```

Before running autonomous experiments, review `loop_program.md`, use a dedicated Codex budget, and confirm the configured `codex` command works locally. The example loop optimizes test/runtime performance while protecting tests, verifier files, credentials, databases, execution code, risk settings, services, and broker state. No experiment is committed, merged, restarted, or deployed automatically.

The design follows the core loop requirements - objective verifier, persistent state, and hard stop conditions - and adds the bilevel outer loop described in the linked workflow. The source thread explains the inner keep/rollback cycle and the outer loop that changes the search process when it stagnates: [Loop Engineering thread](https://threadnavigator.com/thread/2072329149520232639/).

This complements `scripts/agentic_coding_loop.ps1`: the bilevel loop searches and exports a champion patch in isolation; after human review and application, the existing coding loop remains the full compile, test, frontend-build, runtime-contract, and live-smoke promotion gate.

## Next phases


- Intraday-accurate backtesting
- News and catalyst scoring
- Remote alerting for process or broker-connection failures
- Live trading only after paper workflow validation
# AI-BOT-Stock
