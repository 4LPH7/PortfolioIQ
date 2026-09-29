---
phase: 05-make-the-signal-engine-evidence-based
plan: 04
status: complete
commits: 1
completed_at: 2026-09-29T11:03:00Z
---

# Plan 05-04: Rolling Walk-Forward Backtester, Dual Baseline Attribution & Delivery Cost Net Returns Summary

## Overview

Plan 05-04 built PortfolioIQ's rolling walk-forward cross-validation backtesting engine (`src/analytics/walk_forward.py`) with strict zero-lookahead bias. It simulates out-of-sample trading execution with realistic Indian equity delivery transaction fees (STT, DP charges, GST, Stamp Duty), evaluates performance against dual baselines (Stock Buy-and-Hold and NIFTY 50 TRI Buy-and-Hold), and gates signal strategies using evidence hurdles (D-05, D-06) returning `BacktestRunDTO`.

## Key Accomplishments

1. **Rolling Walk-Forward Fold Partitioning (`generate_walk_forward_folds`):**
   - Implemented `generate_walk_forward_folds(total_bars, train_window=252, test_window=63, step=63)`.
   - Guaranteed non-overlapping contiguous out-of-sample test slices with strictly preceding rolling 252-day training windows ($t_{\text{train\_start}} < t_{\text{train\_end}} = t_{\text{test\_start}} < t_{\text{test\_end}}$).
   - Validated that total sequential bars meet or exceed $train\_window + test\_window$, raising a descriptive `ValueError` otherwise.

2. **Zero-Lookahead Calibration & Strategy Simulation (`run_walk_forward_backtest`):**
   - Aligned stock and benchmark dates via DatetimeIndex intersection.
   - For each fold:
     - Truncated trailing forward return horizon bars from training data before calling `evaluate_indicators()`, preventing forward return leakage across fold boundaries.
     - Calibrated indicator weights were frozen and applied exclusively to the subsequent out-of-sample test slice.
     - Simulated causal daily execution: BUY when `composite_score >= 60.0`, SELL when `composite_score <= 40.0`, otherwise HOLD/CASH.
     - Realistic Indian equity delivery charges deducted on each entry and exit via `compute_indian_delivery_charges()` (0.1% STT, 0.015% stamp duty, 18% GST, and ₹15.34 DP charges on exit).
     - Contiguous cumulative net equity curve tracked across all test folds.
     - Marked to market and closed open positions on final bar with full fee accounting.

3. **Dual Baseline Attribution:**
   - **Baseline 1 (Stock Buy-and-Hold):** Evaluated equity buy-and-hold returns over identical out-of-sample test days to measure timing skill.
   - **Baseline 2 (Benchmark Buy-and-Hold):** Evaluated NIFTY 50 TRI buy-and-hold returns over identical out-of-sample test days to measure market alpha.
   - Computed excess CAGR vs Stock and excess CAGR vs Benchmark.

4. **Out-of-Sample Risk & Performance Metrics:**
   - Annualized CAGR, Annualized Sharpe Ratio and Sortino Ratio (using Indian risk-free rate 6.5% and $\sqrt{252}$).
   - Peak-to-trough Maximum Drawdown.
   - Closed trades attribution: Win Rate, Profit Factor, Total Trades, and Annualized Basis-Point Fee Drag (`total_cost_drag_bps`).
   - Evidence hurdle evaluation: tagged `status = "PROVEN_EDGE"` if Strategy outperforms both baselines with positive win rate; otherwise tagged `status = "UNPROVEN_NOISE"`.
   - Output structured as `BacktestRunDTO`.

5. **Comprehensive Verification & Quality Gates:**
   - Authored `tests/test_walk_forward.py` with 10 unit tests covering fold generation, temporal isolation, transaction fee deductions, dual baselines, metric formulas, non-datetime indices, disjoint benchmark fallbacks, and edge-case handling when all indicators fail.
   - 279 total tests passing in 13.70s.
   - 100% safety-critical coverage maintained across `src/execution/`, `src/config/`, and `src/api/middleware.py`.
   - Zero Ruff lint or format issues across 143 files.
