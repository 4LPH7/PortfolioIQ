# PortfolioIQ 📈

> **Self-Hosted Institutional-Grade Personal Equity Intelligence & Autonomous Portfolio Rebalancing for Indian Equities (NSE/BSE)**

[![CI](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-446%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/test_coverage-79%25-brightgreen.svg)]()
[![Critical Path Coverage](https://img.shields.io/badge/safety_critical_coverage-98.9%25-success.svg)]()
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Gitleaks](https://img.shields.io/badge/security-gitleaks-blueviolet.svg)](https://github.com/gitleaks/gitleaks)
[![Self-Hosted](https://img.shields.io/badge/deployment-100%25%20self--hosted-purple.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

PortfolioIQ is a 100% self-hosted, institutional-grade quantitative portfolio management, tax-aware rebalancing, and algorithmic signal engine purpose-built for Indian retail equities. It runs entirely on your own hardware or private home server—ensuring your Zerodha broker credentials, access tokens, holdings, trade history, and tax lots remain strictly private.

---

## ⚡ Quick Start: One Command to Run Everything

PortfolioIQ launches its entire stack—PostgreSQL database, Python quantitative API engine, background market poller, and responsive frontend dashboard—with **a single command**:

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running) and Python 3.10+ for the launcher. Nothing else — Postgres, the API and the web server all run in containers.

### 🚀 Primary Launcher (Cross-Platform)
```bash
python run.py
```
*Creates `.env` on first run (with a random unlock key), builds the containers, applies database migrations, waits for the API to be healthy, prints your unlock key and opens the dashboard.*

### 🔓 First run
1. The dashboard asks for an **unlock key**. It's printed by `python run.py` and stored as `PORTFOLIOIQ_API_KEY` in `.env`.
2. Import holdings from a broker CSV in **Onboarding** (works without a Zerodha account; an `LTP` column sets current prices and P&L), or connect Kite (see below) for live quotes.
3. Orders are always simulated until you deliberately set `DRY_RUN_MODE=false` — see [`docs/GRADUATION_CHECKLIST.md`](docs/GRADUATION_CHECKLIST.md).

### 🛠️ One-Command Launcher CLI Options
| Command | Mode | Description |
| :--- | :--- | :--- |
| `python run.py` | **Standard** | Builds & starts containers, opens browser, streams live container logs |
| `python run.py -d` | **Background** | Starts all containers in detached mode and exits |
| `python run.py --status` | **Health Probe** | Displays live container states and probes API & UI endpoints |
| `python run.py --stop` | **Stop** | Gracefully terminates and removes all running containers |
| `python run.py --local` | **Native Dev** | Runs migrations and unified Flask server natively without Docker |
| `python run.py --no-browser` | **Headless** | Starts containers without launching a browser window |

### 💻 Quick-Launch Shortcuts
- **Windows**: Double-click `start.bat`
- **macOS / Linux**: Run `./start.sh`
- **Docker Compose Direct**: Run `docker compose up --build`

### 🌐 Access Your Services
| Component | Local URL | Description |
| :--- | :--- | :--- |
| **Frontend UI** | [**http://localhost:8080**](http://localhost:8080) | Full responsive glassmorphic dashboard (Nginx reverse-proxy) |
| **Unified Web App** | [**http://localhost:5000**](http://localhost:5000) | Direct Flask backend serving both REST API and static UI |
| **API Health Status** | [**http://localhost:5000/api/v1/health**](http://localhost:5000/api/v1/health) | System health and database connectivity probe |
| **Database GUI** | [**http://localhost:5050**](http://localhost:5050) | Optional pgAdmin, localhost only (`docker compose --profile tools up -d`) |
| **PostgreSQL Port** | `127.0.0.1:5434` (host) | Isolated local database (configurable via `POSTGRES_PORT` in `.env`) |

To stop all services anytime (your data stays in the `pgdata` Docker volume):
```bash
python run.py --stop   # or: docker compose down
```

### 🩺 Troubleshooting
| Symptom | Fix |
| :--- | :--- |
| `Docker is installed but not running` | Start Docker Desktop, wait for it to say *running*, re-run `python run.py`. |
| Port 5000 / 8080 already in use | Another app holds the port. Stop it, or change the host port in `docker-compose.yml`. |
| Forgot the unlock key | It's `PORTFOLIOIQ_API_KEY` in `.env`, and `python run.py` prints it every time it starts. |
| Backend restarts in a loop | A migration failed — `docker compose logs backend` shows which one. The app refuses to start on a half-migrated database. |
| Market treated as open on a holiday | The NSE holiday calendar ships through **2026**; add the next year's dates as a new migration when NSE publishes them. |

---

## 🏛️ Self-Hosted Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              LOCAL MACHINE                                  │
│                                                                             │
│  User Browser                                                               │
│       │                                                                     │
│       ▼                                                                     │
│  Frontend Dashboard (Nginx :8080 / Flask :5000)                             │
│       │                                                                     │
│       ▼                                                                     │
│  PortfolioIQ REST API & Quant Engine (Python 3.12 / Flask :5000)             │
│       │                                       │                             │
│       ├─▶ PostgreSQL 16 (Port :5434 / :5432)  ├─▶ Zerodha Kite Connect API   │
│       │   • user_holdings                     │   • OAuth session exchange  │
│       │   • user_positions                    │   • Live LTP & quote polling│
│       │   • user_margins                      │   • Holdings & SOD sync     │
│       │   • live_prices & price_history       │   • Dry-run / live orders   │
│       │   • tax_lots & cash_flows             │                             │
│       │   • order_audit_trail                 └─▶ Direct Yahoo Chart Cache  │
│       │   • schema_migrations (idempotent)        (historical fallback)     │
│       │                                                                     │
│       ▼                                                                     │
│  Background Scheduler (APScheduler)                                         │
│       • Market open/close countdown & calendar awareness                    │
│       • Periodic quote poller during market hours (09:15–15:30 IST)         │
│       • End-of-day equity curve snapshots & alpha attribution                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 🔒 100% Private, On-Premise Data Privacy
- **Zero Cloud Footprint**: No third-party SaaS servers, no cloud hosting dependencies, no telemetry phone-home, no external deployments.
- **Fail-Closed Execution**: Operates in `DRY_RUN_MODE=true` by default. Real orders can only be placed if explicitly enabled and authorized with a safety verification token.
- **Local Isolation**: Broker API credentials, TOTP keys, request tokens, and account numbers reside strictly inside your local `.env` and PostgreSQL volume.

---

## 📱 The 10 Self-Hosted Dashboard Pages

PortfolioIQ features a responsive obsidian-dark design system built with native **Cross-Document View Transitions**, fluid **physics-based spring easing**, and accessible WCAG 2.1 AA compliant typography:

| Page | URL | Core Capabilities |
| :--- | :--- | :--- |
| **🏠 Dashboard** | [`/index.html`](frontend/index.html) | Real-time Net Asset Value (NAV), Total Equity, P&L KPIs, interactive sector allocation donut, equity market treemap, live quote poller, and quick execution actions. |
| **🔬 Stock Analyzer** | [`/analyzer.html`](frontend/analyzer.html) | Evidence-based quantitative engine: walk-forward backtested indicator ranking (RSI, MACD, Bollinger Bands, Linear Regression), Spearman IC Information Ratio weighting, Student's t calibrated Monte Carlo cones (P10–P90), and dual benchmark comparisons. |
| **⚖️ Smart Rebalancer** | [`/rebalance.html`](frontend/rebalance.html) | Algorithmic target allocation rebalancing with Indian statutory transaction cost modeling, 2% cash reserve floor, 15% daily turnover cap, 1% 20-day ADV liquidity constraints, and dry-run execution manifests. |
| **🛡️ Tax Guard** | [`/tax.html`](frontend/tax.html) | FIFO tax-lot tracking under Indian Finance Act provisions (STCG @ 20%, LTCG @ 12.5% above ₹1.25 Lakh exemption), 30-day conversion warnings, and automated tax-loss harvesting lot proposals. |
| **📋 Audit Log** | [`/audit.html`](frontend/audit.html) | Append-only immutable log of every order recommendation, pre-trade gatekeeper validation check, and broker response with full cryptographic auditability and CSV export. |
| **📅 Portfolio Timeline** | [`/timeline.html`](frontend/timeline.html) | Interactive equity curve charting portfolio Time-Weighted Return (TWR) vs NIFTY 50 TRI benchmark with event pin markers for deposits, withdrawals, and rebalance trades. |
| **⚡ System Status** | [`/status.html`](frontend/status.html) | Real-time health diagnostic dashboard: PostgreSQL database connectivity, Zerodha broker session verification, NSE trading countdown timer, and Phase 8 trading safety certification gates. |
| **📑 Monthly Report** | [`/report.html`](frontend/report.html) | Executive monthly performance statement formatted for A4/PDF print and export featuring Sharpe Ratio, Sortino Ratio, Maximum Drawdown, Jensen's Alpha, Beta, and cost attribution. |
| **⚙️ System Settings** | [`/settings.html`](frontend/settings.html) | Runtime configuration editor for gatekeeper slippage bounds, concentration limits, database migration status, price partition inspection, and session management. |
| **🚀 Onboarding Wizard** | [`/onboarding.html`](frontend/onboarding.html) | Guided first-run setup for the Kite connection, broker or CSV portfolio import, and target allocations. |

---

## 🧮 Quantitative Signal Engine & Evidence Gating

Unlike conventional bots that rely on arbitrary heuristic triggers, PortfolioIQ gates all signals through empirical quantitative rigor:

1. **Walk-Forward Cross-Validation (252-Day Train / 63-Day Test):**  
   Evaluates each technical indicator independently across historical rolling out-of-sample periods with zero lookahead bias.
2. **Spearman Rank Information Coefficient (IC) & Pruning:**  
   Calculates the Spearman rank correlation between indicator rank and future forward returns (5, 20, 60 trading days). Indicators with mean out-of-sample $\text{IC} \le 0$ or $p > 0.05$ are automatically pruned to weight 0.0.
3. **Dynamic Information Ratio (IR) Weighting:**  
   Active indicators are weighted proportionally to their predictive signal-to-noise ratio:  
   $$\text{IR} = \frac{\overline{\text{IC}}}{\sigma_{\text{IC}}}$$
4. **Calibrated Fat-Tailed Monte Carlo Simulation:**  
   Replaces Gaussian geometric Brownian motion with **Student's t distributions** fitted to empirical kurtosis and skewness, producing un-biased probabilistic dispersion percentiles ($P_{10}, P_{25}, P_{50}, P_{75}, P_{90}$).
5. **Rebalancer Evidence Gate:**  
   Tactical buy/sell suggestions are labeled as `PROVEN_EDGE` or `UNPROVEN_NOISE`. Signals categorized as `UNPROVEN_NOISE` are automatically suppressed from generating rebalancing orders, preventing costly turnover on unproven alpha.

---

## 💰 Indian Market Friction & Statutory Cost Calculator

All trade recommendations and rebalancing proposals calculate complete statutory Indian equity delivery costs:

| Cost Component | Statutory Rate / Formula | Impact |
| :--- | :--- | :--- |
| **Securities Transaction Tax (STT)** | 0.1% on buy and sell value | Equity Delivery |
| **NSE Exchange Turnover Charge** | 0.00297% of trade turnover | Exchange fee |
| **SEBI Turnover Fee** | 0.0001% of trade turnover | Regulatory fee |
| **Stamp Duty** | 0.015% on buy turnover | State stamp duty |
| **Goods & Services Tax (GST)** | 18% on (Brokerage + Exchange Fees + SEBI Fees) | Indirect tax |
| **Depository Participant (DP) Charges** | ₹15.34 (flat fee per scrip per day on debit/sell) | CDSL debit charge |

---

## 🔑 Zerodha Kite Connect Configuration

PortfolioIQ uses the official Kite Connect API for live quotes, Start-of-Day (SOD) holdings sync, positions, and margin tracking.

1. **Create an App in Kite Developer Console**:
   - Go to [developers.kite.trade](https://developers.kite.trade/apps).
   - Create a new app.
   - Set the **Redirect URL** to:
     ```
     http://127.0.0.1:5000/api/v1/broker/callback
     ```
2. **Configure Your `.env` File**:
   Copy `.env.example` to `.env` (handled automatically on first run) and enter your credentials:
   ```env
   KITE_API_KEY=your_kite_api_key_here
   KITE_API_SECRET=your_kite_api_secret_here
   KITE_REDIRECT_URL=http://127.0.0.1:5000/api/v1/broker/callback
   DRY_RUN_MODE=true
   ```
3. **Daily Authentication**:
   - Open **Settings → Broker connection** (or click the Kite login button on the top shelf).
   - Log in with your Zerodha credentials.
   - Kite will securely redirect to `http://127.0.0.1:5000/api/v1/broker/callback` and complete the token handshake.
   - Your holdings, margins, and positions sync instantly into your local PostgreSQL database.

---

## 🛠️ Manual / Development Setup (Without Docker)

If you prefer running PortfolioIQ directly with native Python and PostgreSQL:

```bash
# 1. Clone repository
git clone https://github.com/4LPH7/PortfolioIQ.git
cd PortfolioIQ

# 2. Create virtual environment & install dependencies
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 3. Setup configuration & database
cp .env.example .env
python db/run_migrations.py

# 4. Start the application
python run.py --local
```

---

## 🧪 Testing & Quality Gates

PortfolioIQ maintains an institutional-grade test suite with 100% passing tests and strict safety certification:

```bash
# Run complete test suite (446 unit & integration tests)
pytest tests/ -v

# Run with test coverage enforcement (>= 75% overall)
pytest tests/ --cov=src --cov-fail-under=75 -v

# Verify safety-critical execution path coverage (>= 85% on order routing & gatekeeper)
python scripts/check_critical_coverage.py

# Check code style with Ruff
ruff check .
ruff format --check .

# Run dependency vulnerability audit
pip-audit -r requirements.txt --ignore-vuln PYSEC-2020-25 --ignore-vuln CVE-2026-77528 --ignore-vuln CVE-2026-106454
```
*(CVE-2026-106454 is in twisted's IMAP server, pulled in by `kiteconnect`; PortfolioIQ never runs IMAP. Remove the ignore once twisted ships a fix.)*

### Safety Certification Metrics
- **Total Test Cases**: **446 passed** (0 failures, 0 regressions)
- **Overall Code Coverage**: **79%** (CI gate: 75%)
- **Critical Path Gatekeeper Coverage**: **100.00%**
- **Slippage Validator Coverage**: **96.72%**
- **Trading Safety Gatekeeper**: **98.90%**
- **Secret Leaks**: **0 detected** (Gitleaks verified)

---

## ⚙️ Environment Variables Reference

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | String | *Required* | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`) |
| `KITE_API_KEY` | String | *Required* | Zerodha Kite Connect developer API key |
| `KITE_API_SECRET` | String | *Required* | Zerodha Kite Connect developer API secret |
| `PORTFOLIOIQ_API_KEY` | String | random, generated on first run | Dashboard unlock key, sent in the `X-API-Key` HTTP header |
| `DRY_RUN_MODE` | Boolean | `true` | **Safety Core**: When `true`, all orders are simulated. Must remain `true` until certified. |
| `ALLOWED_ORIGINS` | String | `http://localhost:8080,http://127.0.0.1:5000` | Comma-separated list of allowed browser origins |
| `FRONTEND_ORIGIN` | URL | `http://localhost:8080` | Frontend origin used after successful broker sign-in |
| `APP_ENV` | String | `development` | Environment mode (`development` or `production`) |
| `LOG_LEVEL` | String | `INFO` | Loguru logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `POLLING_INTERVAL_SEC` | Integer | `15` | Minimum interval between Kite quote snapshot requests in seconds |
| `SLIPPAGE_BOUND_PCT` | Float | `2.0` | Maximum allowable price drift percentage before blocking an order |
| `CONCENTRATION_LIMIT_PCT`| Float | `15.0` | Maximum single-stock portfolio weight percentage limit |
| `DUPLICATE_WINDOW_SEC` | Integer | `300` | Lookback window in seconds to block duplicate order submissions |
| `TIMEZONE` | String | `Asia/Kolkata` | Standard time zone for market session scheduling |

---

## 🔒 Security & Safe Trading Guardrails

- **Zero Hardcoded Secrets**: All keys, passwords, and tokens are injected strictly via environment variables and checked via `gitleaks` in pre-commit and CI.
- **Fail-Closed Gatekeeper**: The execution layer operates in strict `DRY_RUN_MODE=true` by default. Order routing requires multi-step validation checks (stale price check, slippage boundary, concentration limit, duplicate window, and margin sufficiency).
- **Multi-Tenant Data Isolation**: Database schemas enforce foreign keys on `user_id` across holdings, tax lots, audit trails, and portfolio snapshots.
- **Append-Only Audit Trail**: Every trade proposal, gatekeeper rejection reason, and execution trace is recorded to immutable tables for compliance review.

---

## 📄 License & Disclaimer

Distributed under the **MIT License**. See `LICENSE` for more information.

> **Disclaimer**: *PortfolioIQ is designed for personal algorithmic portfolio analysis and paper-trading simulation. Quantitative signals and probabilistic distributions are statistical outputs based on historical market data and do not guarantee future performance. Never risk capital you cannot afford to lose.*
