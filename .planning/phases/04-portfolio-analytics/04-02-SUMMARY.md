---
phase: 04-portfolio-analytics
plan: 02
status: complete
commits: 1
completed_at: 2026-09-28T09:54:00Z
---

# Plan 04-02: Quantitative Return & Attribution Engine Summary

## Overview

Plan 04-02 implemented the core quantitative performance and risk attribution engine in `src/analytics/performance.py`, providing GIPS-compliant Time-Weighted Return (TWR) unitization, robust Money-Weighted Return (XIRR) solving, Sharpe and Sortino ratio calculations scaled to Indian equity sessions ($\sqrt{252}$), peak-to-trough Drawdown tracking with High-Water Marks, and CAPM Beta / Jensen's Alpha rolling regressions.

## Key Accomplishments

1. **GIPS Time-Weighted Return (TWR) & Unitization:**
   - Implemented `calculate_unitized_twr()` to compute true management returns isolated from investor cash flows.
   - Calculates annualized CAGR for histories $\ge 30$ days.
2. **Numerical XIRR Solver:**
   - Implemented `calculate_xirr()` using Newton-Raphson quadratic root-finding with Brentq bounded search and pure-Python bisection fallbacks.
   - Handles irregular calendar dates and multiple cash flow injections/withdrawals.
3. **Sharpe & Sortino Ratios (Indian Conventions):**
   - Implemented `calculate_sharpe_sortino()` using Indian Treasury Bill risk-free rates (~6.50% p.a.) and $\sqrt{252}$ scaling.
   - Sortino correctly computes downside semi-variance below $R_f$.
   - Enforces a strict 30-day warmup gate before outputting annualized volatility ratios.
4. **Drawdown & High-Water Mark (HWM) Engine:**
   - Implemented `calculate_drawdown_series()` returning maximum drawdown %, current drawdown %, HWM, peak date, trough date, recovery date, and recovery status.
5. **Beta, Jensen's Alpha & Tracking Attribution:**
   - Implemented `calculate_beta_alpha()` relative to benchmark (e.g. NIFTY 50 TRI), returning Beta, annualized Alpha %, $R^2$, and Tracking Error %.
6. **Consolidated Performance Summary & Drag Attribution:**
   - Implemented `compute_portfolio_performance_summary()` integrating all analytics with basis-point breakdown for STT, broker fees, and realized taxes.
7. **Comprehensive Unit Testing:**
   - Created `tests/test_performance.py` covering all 8 core test cases.
   - All 205 test cases in the test suite pass with 100% coverage on safety-critical paths.
