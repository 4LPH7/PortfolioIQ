# Phase 04: Portfolio Analytics That Actually Mean Something - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.  
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-28  
**Phase:** 04-portfolio-analytics  
**Areas discussed:** Performance Return Methodology & Benchmarks, Historical NAV Ledger & Cash Flows, Rebalancer Execution Constraints, Transaction Cost & Tax-Aware Optimization  

---

## 1. Performance Return Methodology & Benchmarks

| Option | Description | Selected |
|--------|-------------|----------|
| **Dual Metric (TWR + XIRR)** | TWR isolates management/selection performance; XIRR measures personalized rupee-weighted return | ✓ |
| **TWR Only** | Strict institutional standard (GIPS) | |
| **XIRR Only** | Standard retail money-weighted return | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Configurable Benchmark (Default NIFTY 50 TRI, support NIFTY 500 TRI)** | Uses Total Return Index (dividends included) for large-cap or multi-cap comparability | ✓ |
| **NIFTY 50 TRI Only** | Single large-cap baseline | |
| **NIFTY 500 TRI Only** | 500-stock broad market baseline | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Configurable Indian T-Bill Rate (Default: 6.50% annualized)** | Reflects prevailing Indian government risk-free rate with settings override | ✓ |
| **Fixed Constant Rate** | Fixed baseline (e.g. 6.00%) | |
| **Daily Overnight MIBOR/TREPS** | Dynamic money-market rate | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Daily EOD Snapshots with 30-Day Warmup Gate** | Compute daily at close; require 30 trading days before showing annualized Sharpe/Sortino/Beta | ✓ |
| **Real-time Intraday Compute** | Recompute on every price poll | |
| **Weekly Compute** | Compute only on Friday close | |

**Notes:** User chose all recommended options for solid institutional-grade attribution with retail applicability.

---

## 2. Historical NAV Ledger & Cash Flows

| Option | Description | Selected |
|--------|-------------|----------|
| **Dedicated `portfolio_daily_snapshots` Table** | Unitized NAV (base 100), daily return %, benchmark close, and net external flows | ✓ |
| **Dynamic On-The-Fly Reconstruction** | Replay all historical orders against prices on each query | |
| **Daily Full JSON State Snapshot** | Store complete holding breakdown JSON per day | |

| Option | Description | Selected |
|--------|-------------|----------|
| **`portfolio_cash_flows` Table + Auto Margin Delta Detection** | Explicit ledger + daily sync cash margin delta detection + REST API | ✓ |
| **Manual Entry Only via UI/API** | Users manually log cash transfers | |
| **Broker Statement CSV Upload** | Ingest cash flows via monthly CSV statement | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Total Return Accounting** | Cash dividends credited to cash balance; stock splits/bonuses adjust tax-lot cost bases | ✓ |
| **Price Return Only** | Ignore dividends | |
| **Separate P&L Bucket for Dividends** | Keep dividends outside NAV | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Smart Backfill from Trade History (Fallback: Day-1 Baseline)** | Reconstruct past daily NAVs from `order_audit_trail` where possible, or anchor Day 1 baseline | ✓ |
| **Strict Forward-Only Tracking** | Start tracking from today only | |
| **Manual Initial Capital Prompt** | Prompt user for start capital | |

---

## 3. Rebalancer Execution Constraints

| Option | Description | Selected |
|--------|-------------|----------|
| **Configurable Minimum Trade Threshold (Default: ₹2,000)** | Suppress trades < ₹2,000 to eliminate fee drag; full liquidations exempt | ✓ |
| **Dynamic % of AUM** | Scales min trade with portfolio size | |
| **No Minimum Threshold** | Rebalance regardless of order size | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Configurable Cash Buffer (Default: 2.0% of AUM or ₹5,000 floor)** | Protect against margin deficit, fees, and price spikes | ✓ |
| **Fixed Rupee Floor Only** | Fixed ₹5,000 cash floor | |
| **Fully Invested (0% Buffer)** | Deploy 100% of cash | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Daily Turnover Cap (Default: 15% of AUM) + Drift Priority** | Limit session trade volume to 15% AUM; execute largest drift breaches first | ✓ |
| **Order Count Cap** | Max 5 orders per session | |
| **Uncapped Turnover** | Rebalance all drift immediately | |

| Option | Description | Selected |
|--------|-------------|----------|
| **ADV Safety Cap (<= 1.0% of 20-Day ADV)** | Size orders <= 1% ADV to prevent market impact and slippage | ✓ |
| **Advisory Warning Only** | Place full order with warning flag | |
| **No ADV Check** | Assume unlimited liquidity | |

---

## 4. Transaction Cost & Tax-Aware Optimization

| Option | Description | Selected |
|--------|-------------|----------|
| **Full Indian Statutory & Delivery Fee Schedule** | STT 0.1%, NSE fee 0.00297%, SEBI fee 0.0001%, Stamp duty 0.015%, GST 18%, DP charges ₹15.34 | ✓ |
| **Simplified Flat Rate** | Flat 0.25% approximation | |
| **STT Only** | 0.1% STT only | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Tax-Optimized Lot Selection (Highest-Cost / Loss-First + LTCG Protection)** | Sell loss lots first to harvest STCL; protect lots within 30 days of 365-day LTCG threshold | ✓ |
| **Strict FIFO** | Sell oldest tax lot first | |
| **Advisory Report Only** | Standard FIFO execution with separate tax harvesting recommendations | |

| Option | Description | Selected |
|--------|-------------|----------|
| **FY Realized LTCG Tracker + Tax-Gain Harvesting Recommendations** | Track annual ₹1.25L exemption; suggest Q4 tax-free gain harvesting | ✓ |
| **Passive Accounting Only** | Factor ₹1.25L into net tax calculations only | |
| **Flat Rate Assumption** | Flat 12.5% tax without exemption tracking | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Dual Pre-Tax & Post-Tax Analytics with Drag Attribution** | Show Gross Return vs Net Post-Tax/Fee Return with bps drag breakdown | ✓ |
| **Gross Primary with Tax Card** | Performance curves show gross only | |
| **Net Return Only** | Everything reported net | |

---

## Deferred Ideas

- Intraday tick-by-tick Sharpe ratio calculations (deferred to Phase 7: Scale).
- Machine-learning predictive alpha signals (Phase 5: Make the Signal Engine Evidence-Based).
- Multi-account / multi-user tax aggregation (Phase 7: Multi-User & Scale).
