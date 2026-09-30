# PortfolioIQ 📈

> **Institutional-Grade Personal Equity Intelligence & Autonomous Portfolio Rebalancing for Indian Markets (NSE/BSE)**

[![CI/CD Pipeline](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml/badge.svg)](https://github.com/4LPH7/PortfolioIQ/actions/workflows/ci.yml)
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
                    ┌────────────────────────────────────────────────────────┐
                    │                   Netlify Edge CDN                     │
                    │   Vanilla HTML5 + CSS3 + Plotly.js + View Transitions  │
                    └───────────────────────────┬────────────────────────────┘
                                                │ HTTPS REST (X-API-Key)
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           Flask API v1 Gateway (Render / Railway)                       │
│  Dual-mounted (/api/v1 & /api) · Pydantic DTOs · Flask-Limiter · Correlation Tracking   │
├───────────────────────────────┬─────────────────────────────┬───────────────────────────┤
│       Signal Engine           │     Tax Guard & Engine      │     Trading Gatekeeper    │
│  • Walk-Forward Backtesting   │  • STCG (20%) / LTCG (12.5%)│  • Slippage & Stale Bounds│
│  • Spearman IC Ranking & IR   │  • ₹1.25L Annual Exemption  │  • Portfolio Concentration│
│  • Fat-Tailed Student's t     │  • Tax-Loss Harvesting      │  • Margin & Cash Reserve  │
│  • Calibrated Monte Carlo     │  • 30-Day Conversion Alerts │  • Idempotency & De-dupe  │
└──────────────┬────────────────┴──────────────┬──────────────┴─────────────┬─────────────┘
               │                               │                            │
               ▼                               ▼                            ▼
┌───────────────────────────────┐ ┌─────────────────────────────┐ ┌───────────────────────┐
│     PostgreSQL 16 Database    │ │   Zerodha Kite Connect API  │ │   Historical Data &   │
│  • 21 Idempotent Migrations   │ │  • REST Holdings Sync (T1)  │ │   Market Status       │
│  • Multi-User Isolated Tables │ │  • Live Quotes & Quotes Poll│ │  • NSE Holiday Calendar │
│  • Immutable Audit Append-Only│ │  • Authenticated OAuth Flow │ │  • Dual Benchmark Tri   │
└───────────────────────────────┘ └─────────────────────────────┘ └───────────────────────┘
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
| **🚀 Onboarding Wizard** | [`/onboarding.html`](frontend/onboarding.html) | 4-step guided first-run experience: Zerodha API credentials validation, database connection check, allocation profile selection, and mock portfolio import. |

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

## 🚢 Deployment Guide

PortfolioIQ is architected for zero-cost, high-reliability deployment using **Render** (free web service for backend API) and **Netlify** (free global edge for frontend).

### Option A: Render Backend (Recommended 100% Free Tier)

Render provides a permanent free web service tier (750 free instance hours/month) with zero credit card requirement:

1. **Fork or Push** this repository to your GitHub account.
2. Log in to [Render.com](https://render.com) and click **New +** → **Blueprint**.
3. Select your `PortfolioIQ` repository. Render will automatically detect [`render.yaml`](render.yaml) and configure the web service.
4. Fill in your environment variables in the Render dashboard:
   - `DATABASE_URL`: Your Supabase or PostgreSQL connection string
   - `KITE_API_KEY` & `KITE_API_SECRET`: Zerodha Kite Connect credentials
   - `PORTFOLIOIQ_API_KEY`: Secure API key for frontend authentication
   - `ALLOWED_ORIGINS`: Your Netlify frontend domain (e.g. `https://your-site.netlify.app`)
5. **Enable Automated CI/CD Deployments:**
   - In Render, navigate to your web service → **Settings** → **Deploy Hook**.
   - Copy the Deploy Hook URL (`https://api.render.com/deploy/srv-xxxx?key=yyyy`).
   - In GitHub, go to **Settings** → **Secrets and variables** → **Actions**.
   - Add secret: `RENDER_DEPLOY_HOOK_URL` = `<your-render-deploy-hook-url>`
   - Add secret: `RENDER_APP_URL` = `https://<your-service>.onrender.com`
   - Every push to `main` will now automatically test, lint, and deploy your API!

---

### Option B: Railway Backend (Project Token Setup)

If deploying to Railway:

> ⚠️ **Important Note on Railway Token Types:**  
> Railway requires a **Project Token**, not an Account Token, for automated CLI deployments.  
> 1. In Railway, open your project → click the **Settings** tab.  
> 2. Select **Tokens** from the sidebar → click **New Token** (Environment: `production`).  
> 3. Copy the token and save it in GitHub Secrets as `RAILWAY_TOKEN`.  
> 4. Save your Railway app URL in GitHub Secrets as `RAILWAY_APP_URL`.  
> *(Note: If your 30-day Railway trial has reached "0 days left", Railway will pause deployments until a billing method or plan is activated).*

---

### Frontend Deployment (Netlify)

The `frontend/` directory is 100% static HTML5/CSS/JavaScript requiring zero build step:

1. In [Netlify](https://app.netlify.com), click **Add new site** → **Import an existing project**.
2. Select your GitHub repository.
3. Set **Publish directory** to: `frontend` (leave Build command blank).
4. Deploy site!
5. In GitHub Secrets, configure `NETLIFY_AUTH_TOKEN` and `NETLIFY_SITE_ID` to enable automated frontend deployments via GitHub Actions.

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
| `PORTFOLIOIQ_API_KEY` | String | `dev-secret-key` | Authentication key passed in `X-API-Key` HTTP header |
| `DRY_RUN_MODE` | Boolean | `true` | **Safety Core**: When `true`, all orders are simulated. Must remain `true` until certified. |
| `ALLOWED_ORIGINS` | String | `*` | Comma-separated list of allowed CORS origins (e.g. Netlify URL) |
| `APP_ENV` | String | `development` | Environment mode (`development` or `production`) |
| `LOG_LEVEL` | String | `INFO` | Loguru logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `POLLING_INTERVAL_SEC` | Integer | `60` | Background market quote polling interval in seconds |
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
