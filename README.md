# PortfolioIQ 📈

> **Institutional-Grade Personal Equity Intelligence & Autonomous Portfolio Rebalancing for Indian Markets (NSE/BSE)**

[![CI](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/test_coverage-83%25-brightgreen.svg)]()
[![Critical Path Coverage](https://img.shields.io/badge/safety_critical_coverage-98.9%25-success.svg)]()
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Gitleaks](https://img.shields.io/badge/security-gitleaks-blueviolet.svg)](https://github.com/gitleaks/gitleaks)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

PortfolioIQ is a production-grade algorithmic portfolio management, tax-aware rebalancing, and quantitative signal platform purpose-built for Indian retail equities. It integrates directly with **Zerodha Kite Connect**, models granular Indian market statutory transaction friction (STT, DP charges, GST, SEBI fees, stamp duty), tracks capital gains tax lots under the latest Budget tax provisions, and enforces strict pre-trade validation gates before routing simulated or live orders.

---

## 🏛️ System Architecture

```
GitHub repository ──CI passes on main──▶ GitHub Pages
                                           Static UI preview only
                                           (no API or private data)

For live personal use:
Browser ──▶ Docker frontend ──▶ Flask API ──▶ PostgreSQL
                                  ├──────────▶ Kite Connect
                                  └──────────▶ market data services
```

---

## 📱 The 10 Production Pages

PortfolioIQ features a refined obsidian dark design system built with native **Cross-Document View Transitions**, fluid **physics-based spring easing**, and accessible WCAG 2.1 AA compliant typography:

| Page | URL | Core Capabilities |
| :--- | :--- | :--- |
| **🏠 Dashboard** | [`/index.html`](frontend/index.html) | Real-time Net Asset Value (NAV), Total Equity, P&L KPIs, interactive sector allocation donut, equity market treemap, live quote poller, and quick execution actions. |
| **🔬 Stock Analyzer** | [`/analyzer.html`](frontend/analyzer.html) | Evidence-based quantitative engine: walk-forward backtested indicator ranking (RSI, MACD, Bollinger Bands, Linear Regression), Spearman IC Information Ratio weighting, Student's t calibrated Monte Carlo cones (P10–P90), and dual benchmark comparisons. |
| **⚖️ Smart Rebalancer** | [`/rebalance.html`](frontend/rebalance.html) | Algorithmic target allocation rebalancing with Indian statutory transaction cost modeling, 2% cash reserve floor, 15% daily turnover cap, 1% 20-day ADV liquidity constraints, and dry-run execution manifests. |
| **🛡️ Tax Guard** | [`/tax.html`](frontend/tax.html) | FIFO tax-lot tracking under Indian Finance Act provisions (STCG @ 20%, LTCG @ 12.5% above ₹1.25 Lakh exemption), 30-day conversion warnings, and automated tax-loss harvesting lot proposals. |
| **📋 Audit Log** | [`/audit.html`](frontend/audit.html) | Append-only immutable log of every order recommendation, pre-trade gatekeeper validation check, and broker response with full cryptographic auditability and CSV export. |
| **📅 Portfolio Timeline** | [`/timeline.html`](frontend/timeline.html) | Interactive equity curve charting portfolio Time-Weighted Return (TWR) vs NIFTY 50 TRI benchmark with event pin markers for deposits, withdrawals, and rebalance trades. |
| **⚡ System Status** | [`/status.html`](frontend/status.html) | Real-time health diagnostic dashboard: PostgreSQL database connectivity, Zerodha broker session verification, NSE trading countdown timer, and Phase 8 trading safety certification gates. |
| **📑 Monthly Report** | [`/report.html`](frontend/report.html) | Executive monthly performance statement formatted for A4/PDF export featuring Sharpe Ratio, Sortino Ratio, Maximum Drawdown, Jensen's Alpha, Beta, and cost attribution. |
| **⚙️ System Settings** | [`/settings.html`](frontend/settings.html) | Runtime configuration editor for gatekeeper slippage bounds, concentration limits, database migration status, price partition inspection, and session management. |
| **🚀 Onboarding Wizard** | [`/onboarding.html`](frontend/onboarding.html) | Guided first-run setup for the Kite connection, broker or CSV portfolio import, and target allocations. |

---

## 🧮 Quantitative Signal Engine & Evidence Gating

Unlike conventional bots that rely on arbitrary heuristic triggers, PortfolioIQ gates all signals through empirical rigor:

1. **Walk-Forward Cross-Validation (252-Day Train / 63-Day Test):**  
   Evaluates each technical indicator independently across historical rolling out-of-sample periods with zero lookahead bias.
2. **Spearman Rank Information Coefficient (IC) & Pruning:**  
   Calculates the Spearman rank correlation between indicator rank and future forward returns (5, 20, 60 trading days). Indicators with mean out-of-sample $IC \le 0$ or $p > 0.05$ are automatically pruned to weight 0.0.
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

## 🚢 Hosting and Local Live Use

GitHub Pages can publish the static files in `frontend/`, and the CI workflow deploys them after the main-branch tests and Docker build pass. Enable **Settings → Pages → Build and deployment → GitHub Actions** in the repository. On the free GitHub plan, Pages requires a public repository.

Pages is a static file host. It cannot run the Flask API, perform Kite's server-side login exchange, connect privately to PostgreSQL, or keep a live quote process running. The Pages site is therefore a UI preview only; it cannot show synced holdings, complete Kite login, or update live data. GitHub Actions jobs are temporary build jobs, not an always-on API server. GitHub also says Pages is not intended for SaaS hosting or sensitive transactions. See [GitHub Pages limitations](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).

For a free, usable personal setup, run the backend and frontend together with Docker on your own computer. This keeps broker credentials and portfolio data off the public site and does not require a paid worker. The computer and Docker services must be running for data refreshes.

### Local Docker setup for live Kite data

1. Copy `.env.example` to `.env`, then set `KITE_API_KEY`, `KITE_API_SECRET`, and a long random `PORTFOLIOIQ_API_KEY`. Keep `DRY_RUN_MODE=true`.
2. Start the app with `docker compose up --build` and open `http://localhost:8080`.
3. In the Kite developer console, set the redirect URL to `http://127.0.0.1:5000/api/v1/broker/callback`. Keep `KITE_REDIRECT_URL` at that value and `FRONTEND_ORIGIN=http://localhost:8080` in `.env`.
4. Open **Settings → Broker connection** and sign in to Kite. Kite requires a user login each trading day. A successful login syncs funds, holdings, and positions. While the local dashboard is open, quotes refresh automatically at the configured polling interval.
5. Confirm `http://localhost:5000/api/v1/health` reports a healthy database. Do not put `.env`, Kite credentials, API keys, access tokens, or portfolio exports in GitHub Pages or the repository.

### Database safety

Keep `DATABASE_URL` and any Supabase service-role key in the backend's private environment only; never add them to frontend files or GitHub Pages. Before storing real user data, verify that database access policies match the app's authorization model. Apply schema updates with the repository migrations and verify their recorded versions before use.

---

## 🛠️ Local Development & Quick Start

### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose (or local PostgreSQL 16+)
- Git

### 2. Setup Environment
```bash
# Clone the repository
git clone https://github.com/4LPH7/PortfolioIQ.git
cd PortfolioIQ

# Create and activate virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Database & Migrations

The local Docker Compose database is published on `127.0.0.1:5433` so it can run beside a local PostgreSQL server on the default port. The example `.env` uses the matching Docker database name, user, password, and port.

```bash
# Start local PostgreSQL container
docker-compose up -d postgres

# Copy sample configuration
cp .env.example .env

# Run all 21 idempotent database migrations
python db/run_migrations.py
```

### 4. Start Backend & Frontend
```bash
# Launch Flask REST API (port 5000)
python flask_app.py

# In a separate terminal, serve the frontend
python -m http.server 8080 --directory frontend
```
Navigate to `http://localhost:8080` in your browser. The frontend auto-detects `localhost:5000` for all API calls.

---

## 🧪 Testing & Quality Gates

PortfolioIQ enforces an institutional test suite with zero tolerance for schema drift or untested critical paths:

```bash
# Run complete test suite with coverage
pytest tests/ --cov=src --cov-fail-under=75 -v

# Verify safety-critical path coverage (enforces >= 85% on execution modules)
python scripts/check_critical_coverage.py

# Run Ruff linter and code formatter check
ruff check .
ruff format --check .

# Run pre-deployment dependency vulnerability audit
pip-audit -r requirements.txt --ignore-vuln PYSEC-2020-25 --ignore-vuln CVE-2026-77528
```

### Safety Certification Metrics
- **Total Test Cases**: 437 passed
- **Overall Code Coverage**: 82.91%
- **Critical Path Gatekeeper Coverage**: 100.00%
- **Slippage Validator Coverage**: 96.72%
- **Trading Safety Gatekeeper**: 98.90%
- **Secret Leaks**: 0 detected (Gitleaks verified)

---

## ⚙️ Environment Variables Reference

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | String | *Required* | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`) |
| `KITE_API_KEY` | String | *Required* | Zerodha Kite Connect developer API key |
| `KITE_API_SECRET` | String | *Required* | Zerodha Kite Connect developer API secret |
| `PORTFOLIOIQ_API_KEY` | String | *Required* | Private authentication key passed in `X-API-Key` HTTP header |
| `DRY_RUN_MODE` | Boolean | `true` | **Safety Core**: When `true`, all orders are simulated. Must remain `true` until certified. |
| `ALLOWED_ORIGINS` | String | local development origins | Comma-separated list of allowed browser origins |
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
