# PortfolioIQ technical reference

> The detailed reference that used to be the README: signal engine, backtest realism, scanner, costs, configuration and troubleshooting. The [README](../README.md) is the short tour.

Self-hosted portfolio analytics, tax-aware rebalancing and evidence-based signals for Indian equities (NSE/BSE).

[![CI](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Secrets: Gitleaks](https://img.shields.io/badge/secrets-gitleaks-blueviolet.svg)](https://github.com/gitleaks/gitleaks)

PortfolioIQ runs on your own machine or home server. Your Zerodha credentials, access tokens, holdings, trade history and tax lots stay in a local PostgreSQL database and never leave it. Orders are simulated by default; live trading requires an explicit, multi-step opt-in.

## Contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Dashboard pages](#dashboard-pages)
- [Signal engine](#signal-engine)
- [Backtest realism](#backtest-realism)
- [Market scanner](#market-scanner)
- [Odds, alerts, projection and news](#odds-alerts-projection-and-news)
- [Research committee (optional)](#research-committee-optional)
- [Indian transaction costs](#indian-transaction-costs)
- [Zerodha Kite Connect setup](#zerodha-kite-connect-setup)
- [Configuration](#configuration)
- [Development setup](#development-setup)
- [Testing and quality gates](#testing-and-quality-gates)
- [Safety model](#safety-model)
- [Troubleshooting](#troubleshooting)
- [License and disclaimer](#license-and-disclaimer)

## Quick start

**Requirements:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running) and Python 3.10 or later for the launcher. PostgreSQL, the API and the web server all run in containers.

```bash
python run.py
```

On first run the launcher creates `.env` with a random unlock key, builds the containers, applies database migrations, waits for the API to report healthy, prints the unlock key and opens the dashboard.

1. Enter the **unlock key** when the dashboard asks for it. It is stored as `PORTFOLIOIQ_API_KEY` in `.env` and printed each time `run.py` starts.
2. Import holdings from a broker CSV under **Onboarding** (no Zerodha account needed; an `LTP` column sets current prices), or connect Kite for live quotes.
3. Orders stay simulated until you deliberately set `DRY_RUN_MODE=false` and complete [`docs/GRADUATION_CHECKLIST.md`](docs/GRADUATION_CHECKLIST.md).

### Launcher options

| Command | Description |
| :--- | :--- |
| `python run.py` | Build and start the containers, open the browser, stream logs |
| `python run.py -d` | Start the containers in the background and exit |
| `python run.py --status` | Show container state and probe the API and UI |
| `python run.py --stop` | Stop and remove the containers (data is kept) |
| `python run.py --local` | Run migrations and the Flask server natively, without Docker |
| `python run.py --no-browser` | Start without opening a browser |

`start.sh` (macOS/Linux) and `start.bat` (Windows) are thin wrappers around `run.py`. `docker compose up --build` also works directly.

### Services

| Component | Address | Notes |
| :--- | :--- | :--- |
| Dashboard | http://localhost:8080 | Nginx, proxies `/api/` to the backend (`FRONTEND_PORT` to change) |
| API and UI | http://localhost:5000 | Flask backend; serves the REST API and the static UI |
| Health check | http://localhost:5000/api/v1/health | Database connectivity and holiday-calendar coverage |
| pgAdmin (optional) | http://localhost:5050 | `docker compose --profile tools up -d` |
| PostgreSQL | `127.0.0.1:5434` | Host port, configurable with `POSTGRES_PORT` |

All ports bind to `127.0.0.1`. Data lives in the `pgdata` Docker volume and survives `python run.py --stop`.

## Architecture

```
Browser
  |
  v
Dashboard (Nginx :8080, or Flask :5000)
  |
  v
REST API and analytics engine (Python 3.12, Flask)
  |-- PostgreSQL 16: holdings, tax lots, cash flows, prices, snapshots,
  |                  signals, backtests, research runs, append-only order audit trail
  |-- Zerodha Kite Connect: OAuth session, quotes, holdings and margin sync,
  |                         order placement (simulated unless live mode is certified)
  |-- Yahoo Finance: daily price history, fundamentals and headlines (cached locally)
  |
  v
Scheduler (APScheduler)
  - quote polling during market hours (09:15-15:30 IST, NSE holiday aware)
  - start-of-day sync, end-of-day portfolio and signal snapshots
  - nightly market scan, forward-return settlement, price alerts
```

There is no cloud component and no telemetry. Broker credentials and tokens are read from `.env` and stored only in the local database volume.

## Dashboard pages

| Page | Purpose |
| :--- | :--- |
| [Dashboard](frontend/index.html) | Net asset value, P&L, sector allocation, live quotes, headlines on holdings |
| [Stock Analyzer](frontend/analyzer.html) | Evidence-based signal for one stock: composite score, walk-forward indicators with their out-of-sample evidence, calibrated 30-day price range, stop/target odds with position sizing, price alerts, optional research committee |
| [Market Scanner](frontend/scanner.html) | Nightly NIFTY 500 ranking on signals that passed an out-of-sample test, with the backtest of the top 20 after costs |
| [Rebalancer](frontend/rebalance.html) | Target-allocation rebalancing with Indian transaction costs, cash buffer, turnover and liquidity limits; suggested targets (risk parity, minimum variance, hierarchical risk parity, equal weight) checked walk-forward; saved profiles apply only once activated |
| [Tax Guard](frontend/tax.html) | FIFO tax lots, STCG/LTCG split (20% / 12.5% above the Rs 1.25 lakh exemption), lots nearing the one-year mark, tax-loss harvesting summary |
| [Audit Log](frontend/audit.html) | Every order proposal, pre-trade check and broker response; append-only, enforced by database triggers; CSV export |
| [Timeline](frontend/timeline.html) | Time-weighted return against NIFTY 50 TRI with cash-flow and trade markers, and a 1 to 10-year projection |
| [System Status](frontend/status.html) | Database, broker session, market calendar, scheduler and trading-safety checks |
| [Monthly Report](frontend/report.html) | Printable statement: returns, Sharpe, Sortino, maximum drawdown, alpha, beta, costs |
| [Settings](frontend/settings.html) | Risk limits, research committee caps, migration status, session |
| [Onboarding](frontend/onboarding.html) | First-run setup: Kite connection or CSV import, target allocations |

## Signal engine

Signals are treated as hypotheses to test, not predictions.

1. **Walk-forward validation.** Each indicator (RSI, MACD, Bollinger %B, momentum) is calibrated on a 252-day window and evaluated on the following 63 days, rolling forward with no look-ahead.
2. **Information coefficient and pruning.** The Spearman rank correlation between an indicator and forward returns is measured out of sample. Indicators with a mean IC at or below zero, or p > 0.05, get zero weight.
3. **Weighting.** Surviving indicators are weighted by information ratio, IR = mean(IC) / std(IC).
4. **Calibrated Monte Carlo.** Price ranges use Student's t paths, with the spread scaled until historical 80% cones contained 80% of outcomes. The output is a range (P10 to P90), not a point forecast.
5. **Evidence gate.** Each signal is labelled `PROVEN_EDGE` or `UNPROVEN_NOISE`. The rebalancer suppresses tactical orders on unproven signals.

Daily signal snapshots record 5, 20 and 60-day forward returns against NIFTY 50 TRI once those horizons pass, so the live hit rate can be checked at `GET /api/v1/signals/hit-rate`.

## Backtest realism

The walk-forward signal backtest is built to avoid flattering itself:

- **Execution timing.** A decision made on one day's close fills at the next session's open. A signal on the last day of the test window does not fill.
- **Market impact.** Each fill pays slippage of `min(100, 50 x sqrt(trade value / ADV))` basis points, where ADV is the 20-day average traded value known at the decision. Missing volume data is charged the 100 bps cap. The coefficient is a starting assumption, not fitted to real fills, and is marked as such in `src/analytics/cost_calculator.py`.
- **Statutory costs.** STT, exchange and SEBI fees, stamp duty, GST and DP charges on every trade (see [Indian transaction costs](#indian-transaction-costs)).
- **Reproducibility.** Every run stores an `inputs_hash`: a SHA-256 over its parameters, cost constants, fill rule and the price data it used. Two runs with the same hash used identical inputs.

Results report fees and slippage separately, and are compared against both buy-and-hold of the stock and NIFTY 50 TRI.

## Market scanner

Ranks the NIFTY 500 every weekday at 6:45 PM IST, or on demand. It measures which simple signals have tilted the odds rather than predicting prices.

1. **Signals:** 12-1 and 6-1 month momentum, price against its 200-day average, 50-day against 200-day average, distance from the 52-week high, low volatility and one-month reversal, all from dividend-adjusted prices.
2. **Evidence:** each month, the rank correlation between each signal and the following month's return across all stocks. A signal is used only with a positive average and t > 2, weighted by its consistency.
3. **Out-of-sample backtest:** each past month, the top 20 are chosen using only earlier months' evidence, held for a month with delivery charges and slippage, and compared with an equal-weight hold of every stock. The page leads with the verdict, *tested edge* or *no tested edge*, and notes when the record is short.
4. **Stated limitations:** the first scan downloads ten years of prices per stock (about 10 minutes); today's index members flatter the past (survivorship bias); fundamentals and headlines are shown for context but not scored, because free data has no history to test them against.

## Odds, alerts, projection and news

None of these can place an order.

- **Stop and target odds** (Analyzer). For a planned buy with a stop and a target, 10,000 replays built from blocks of the stock's own trading days, plus the same plan started on every past day, show how often the target was reached first. The verdict compares this with the break-even rate after Indian delivery charges; a gap through the stop fills at the open. With a maximum loss set, it sizes the position, capped by your concentration limit and cash.
- **Price alerts** (Analyzer, notification bell). Rises to, falls to, or moves a set percentage in a day. Each alert fires once and can be re-armed after the price crosses back. Checked every minute in market hours.
- **Portfolio projection** (Timeline). Projects the whole portfolio 1 to 10 years ahead by replaying blocks of past trading days for all holdings together, preserving correlations and fat tails. Shows the outcome range, the chance of ending below today, the worst interim drawdown and the chance of beating NIFTY 50 TRI.
- **Headlines on holdings** (Dashboard). Yahoo Finance headlines every 30 minutes, tagged as results, dividend, deal, rating, regulatory or management news. The feed is unofficial and can lag.

The odds tool adapts ideas from [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT) and [Monte-Carlo-Simulation-for-Stock-Market](https://github.com/theanh97/Monte-Carlo-Simulation-for-Stock-Market) for a long-term delivery portfolio; their intraday features and US-only data sources are not included.

## Research committee (optional)

The Analyzer can ask a committee of Claude agents about one stock: fundamentals, technical and risk analysts, three investor personas (Graham, Buffett, Lynch), a two-round bull/bear debate, and a portfolio manager who gives a rating from Buy to Sell with a conviction score. The structure follows [TradingAgents](https://github.com/TauricResearch/TradingAgents) and ai-hedge-fund; the prompts are written for PortfolioIQ.

- **Advisory only.** The committee has no tools and no code path to orders (enforced by a test). Its output is stored text.
- **Grounded.** It sees only a snapshot of the stock's adjusted prices, your position and recently fetched fundamentals. Every number it cites must reference a snapshot key, and the page shows how many citations matched.
- **Point-in-time.** A snapshot dated in the past only uses fundamentals fetched on or before that date.
- **Track record.** Once a stock has 20 settled runs, the snapshot includes their average 20-day alpha and hit rate, counting only runs whose window closed before the snapshot date.
- **Capped.** Each run has a hard cost cap (default USD 1.00), a daily limit (default 5) and a time limit, configurable under **Settings > Research committee**.
- **Scored.** Each rating is settled against its 5, 20 and 60-day return and alpha over NIFTY 50 TRI. After 15 settled runs the page reports whether conviction ranks returns.

To enable it, add your key to `.env` and restart:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
```

`python scripts/benchmark_committee.py --symbols INFY TCS --spend` compares model choices on your holdings (two paid runs per stock; reports rating agreement, citation accuracy, cost and time).

## Indian transaction costs

Recommendations, rebalancing plans and backtests include full delivery-trade charges:

| Component | Rate |
| :--- | :--- |
| Securities Transaction Tax (STT) | 0.1% on buy and sell value |
| NSE transaction charge | 0.00297% of turnover |
| SEBI turnover fee | 0.0001% of turnover |
| Stamp duty | 0.015% on buy value |
| GST | 18% on brokerage, exchange and SEBI fees |
| DP charge | Rs 15.34 per scrip per day on sells |

## Zerodha Kite Connect setup

PortfolioIQ uses the official Kite Connect API for quotes, start-of-day holdings sync, positions, margins and (in live mode) orders.

1. Create an app at [developers.kite.trade](https://developers.kite.trade/apps) and set its redirect URL to:
   ```
   http://127.0.0.1:5000/api/v1/broker/callback
   ```
2. Add the credentials to `.env` (created from `.env.example` on first run):
   ```env
   KITE_API_KEY=your_kite_api_key
   KITE_API_SECRET=your_kite_api_secret
   DRY_RUN_MODE=true
   ```
3. Each trading day, sign in from **Settings > Broker connection**. Kite redirects back to the callback above, the token exchange completes, and holdings, margins and positions sync to the local database.

## Configuration

Settings are read from environment variables (or `.env`). Names are case-insensitive.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | set by `run.py` / Compose | PostgreSQL connection string |
| `KITE_API_KEY` | required | Kite Connect API key |
| `KITE_API_SECRET` | required | Kite Connect API secret |
| `PORTFOLIOIQ_API_KEY` | random, generated on first run | Dashboard unlock key, sent as the `X-API-Key` header |
| `DRY_RUN_MODE` | `true` | Simulate all orders. See [Safety model](#safety-model) |
| `LIVE_TRADING_CONFIRMATION` | empty | Required confirmation token before live mode will start |
| `ANTHROPIC_API_KEY` | empty | Enables the research committee |
| `ALLOWED_ORIGINS` | `http://localhost:8080,http://localhost:3000,http://127.0.0.1:5000` | Comma-separated browser origins allowed by CORS |
| `FRONTEND_ORIGIN` | `http://localhost:8080` | Where the browser returns after broker sign-in |
| `APP_ENV` | `development` | `development` or `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `TIMEZONE` | `Asia/Kolkata` | Market session time zone |
| `POLLING_INTERVAL_SEC` | `15` | Minimum seconds between Kite quote requests (10 to 300) |
| `SLIPPAGE_BOUND_PCT` | `2.0` | Maximum price drift before a pre-trade check blocks an order |
| `CONCENTRATION_LIMIT_PCT` | `15.0` | Maximum weight of a single stock |
| `DUPLICATE_WINDOW_SEC` | `300` | Window in which an identical order is blocked as a duplicate |
| `POSTGRES_PORT` | `5434` | Host port for PostgreSQL (Docker Compose) |
| `FRONTEND_PORT` | `8080` | Host port for the dashboard (Docker Compose) |

Rebalancer limits (minimum trade value, cash buffer, turnover cap, ADV limit) are defined in `src/config/settings.py` and can be overridden the same way.

## Development setup

To run without Docker, using a local PostgreSQL:

```bash
git clone https://github.com/4LPH7/PortfolioIQ.git
cd PortfolioIQ
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env               # then set DATABASE_URL and the Kite keys
python db/run_migrations.py
python run.py --local
```

## Testing and quality gates

```bash
pytest tests/                                          # unit and integration tests
pytest tests/ --cov=src --cov-fail-under=75            # overall coverage gate
python scripts/check_critical_coverage.py              # 85% gate on safety-critical modules
ruff check . && ruff format --check .                  # lint and formatting
```

Tests marked `postgres` run against a real database and are skipped locally when none is reachable; CI always runs them. CI (`.github/workflows/ci.yml`) also scans the full history for secrets with Gitleaks, audits dependencies with `pip-audit`, applies all migrations to a fresh PostgreSQL and builds the Docker image. The `pip-audit` step ignores a small list of advisories in `twisted`, which `kiteconnect` installs for a websocket client PortfolioIQ never imports; the list and its rationale live in the workflow file. Current test and coverage results are on the [CI page](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml).

## Safety model

- **Dry run by default.** With `DRY_RUN_MODE=true`, every order is validated and logged but never sent to the broker.
- **Live mode is opt-in at startup.** Setting `DRY_RUN_MODE=false` also requires `LIVE_TRADING_CONFIRMATION` and passing the startup safety checks; otherwise the backend refuses to start. Complete [`docs/GRADUATION_CHECKLIST.md`](docs/GRADUATION_CHECKLIST.md) first, including the 14-day dry-run soak (`python scripts/soak_report.py`).
- **Pre-trade gatekeeper.** Every order passes market-hours, quote-freshness, slippage, price-band, concentration, duplicate, sellable-quantity and margin checks. A failed check blocks the order.
- **Append-only audit trail.** Order attempts and check results are written to tables protected by immutability triggers.
- **Per-user isolation.** Holdings, cash flows, snapshots, signals and the audit trail are scoped by `user_id`.
- **No secrets in the repository.** Credentials come from the environment; Gitleaks runs in CI.

## Troubleshooting

| Symptom | Resolution |
| :--- | :--- |
| "Docker is installed but not running" | Start Docker Desktop, wait until it reports running, then re-run `python run.py`. |
| Port 5000 or 8080 already in use | Stop the other application, or change `FRONTEND_PORT` / the port mapping in `docker-compose.yml`. |
| Forgot the unlock key | It is `PORTFOLIOIQ_API_KEY` in `.env`; `python run.py` also prints it on start. |
| Backend restarts in a loop | A migration failed. `docker compose logs backend` names it; the app refuses to start on a partially migrated database. |
| Market treated as open on a holiday | The bundled NSE holiday calendar covers 2026. `/api/v1/health` reports `"calendar_ok": false` when the current year (or, in December, the next year) is missing; add the new dates once NSE publishes them. |

## License and disclaimer

Released under the MIT License.

PortfolioIQ is a tool for personal portfolio analysis and simulated trading. Its signals, backtests and probability ranges are statistical estimates from historical data and do not guarantee future results. It is not investment advice. Never risk capital you cannot afford to lose.
