# PortfolioIQ

> Real-time portfolio analytics, predictive analysis, and smart rebalancing for Indian equities (NSE/BSE).

## Architecture

```
Netlify (frontend/)          Flask API (flask_app.py)         PostgreSQL
  index.html     ──────────>  /api/portfolio/summary   ──────>  user_holdings
  analyzer.html  ──────────>  /api/analysis/<symbol>   ──────>  live_prices
  rebalance.html ──────────>  /api/rebalance/drift     ──────>  allocation_profiles
  tax.html       ──────────>  /api/tax/summary         ──────>  holding_tax_lots
  audit.html     ──────────>  /api/audit/orders        ──────>  order_audit_trail
  settings.html  ──────────>  /api/settings/config     ──────>  system_config
```

## Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | AUM, P&L KPIs, holdings table, sector donut, treemap |
| Stock Analyzer | `/analyzer.html` | RSI, MACD, Bollinger, Linear Regression, Monte Carlo |
| Rebalance | `/rebalance.html` | Drift signals, proposed CNC orders |
| Tax Guard | `/tax.html` | STCG/LTCG lots, near-conversion alerts |
| Audit Log | `/audit.html` | Immutable order history + validation log |
| Settings | `/settings.html` | Config editor, sync, DB stats |

## Prediction Methods

| Method | Parameters | What it measures |
|--------|-----------|-----------------|
| RSI | 14-day Wilder's | Momentum — oversold/overbought |
| MACD | 12/26/9 EMA | Trend crossover strength |
| Bollinger Bands | 20-day ±2σ | Volatility + price positioning |
| Linear Regression | OLS 30-day | Slope + R² trend direction |
| Monte Carlo GBM | 1000 paths, 30-day | Probability distribution of future prices |

## Quick Start

```bash
# 1. Start PostgreSQL and ensure DB is seeded
# 2. Start Flask API backend
python flask_app.py

# 3. Open frontend in browser (any HTTP server)
# Option A: Python
python -m http.server 8080 --directory frontend

# Option B: VS Code Live Server extension
# Open frontend/index.html → Go Live

# Frontend auto-detects localhost:5000 for API
```

## Netlify Deployment

The `frontend/` directory is a static site — no build step.

1. Push this repo to GitHub
2. Import repo into Netlify → Publish directory: `frontend`
3. Set environment / update `API_BASE` in `frontend/js/api.js` to your deployed backend URL

## Holdings Tracked

ITBEES · JPPOWER · GOLDENTOBC-BZ · RPOWER · TATAGOLD · GOLDCASE

## Tech Stack

- **Frontend**: HTML5 · Vanilla CSS · Vanilla JS · Plotly.js
- **Backend**: Python 3.12 · Flask · Flask-CORS · SQLAlchemy 2.0
- **Database**: PostgreSQL 17
- **Analytics**: yfinance · scipy · numpy
- **Broker**: Zerodha Kite Connect API
- **Hosting**: Netlify (frontend) + your chosen backend host

## Disclaimer

This tool is for personal portfolio monitoring only. All prediction outputs are based on historical data and statistical models. They do NOT constitute financial advice.
