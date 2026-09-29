---
phase: 05-make-the-signal-engine-evidence-based
plan: 03
status: complete
commits: 1
completed_at: 2026-09-29T10:48:00Z
---

# Plan 05-03: Quantitative Indicator Evaluator, Spearman Rank IC Engine & Dynamic IR Weighting Summary

## Overview

Plan 05-03 implemented the foundational statistical validation engine for technical indicators in PortfolioIQ. Heuristic indicators are standardized into continuous directional factor scores bounded in `[-1.0, +1.0]`. Individual indicators are evaluated against forward returns via Spearman rank Information Coefficient (IC) and student-t p-values. Statistical pruning automatically eliminates unproven or negative-alpha factors ($IC \le 0$ or $p > 0.05$), while valid indicators are dynamically weighted proportional to their Information Ratio ($IR = \text{mean}(IC) / \text{std}(IC)$).

## Key Accomplishments

1. **Continuous Standardized Factor Scores (`src/analytics/indicator_evaluator.py`):**
   - **RSI Factor (`compute_rsi_factor`):** Wilder's RSI mapped to `[-1.0, +1.0]` via `(50.0 - RSI) / 50.0` with mean-reversion semantics (oversold $<30$ yields bullish positive factor, overbought $>70$ yields bearish negative factor).
   - **MACD Factor (`compute_macd_factor`):** Standard 12/26/9 MACD histogram normalized by rolling 60-day volatility and bounded in `[-1.0, +1.0]` using `tanh(macd_hist / norm_scale)`.
   - **Bollinger Bands Factor (`compute_bollinger_factor`):** Linear scaling of position within bands $\%B$ via `1.0 - 2.0 * %B` bounded in `[-1.0, +1.0]` (lower band = $+1.0$, middle SMA = $0.0$, upper band = $-1.0$).
   - **Momentum Factor (`compute_momentum_factor`):** OLS linear regression slope t-statistic computed via fast 1D convolution and scaled via `tanh(t_stat / 2.0)` bounded in `[-1.0, +1.0]`.
   - **Forward Return Calculator (`compute_forward_returns`):** Strict zero-lookahead shifted percentage return calculator over arbitrary horizons (e.g. 5, 20, 60 days).
   - **Orchestrator (`compute_all_factors`):** Standard dictionary returning all four factor series aligned to the input DataFrame index.

2. **Spearman Rank Information Coefficient (IC) Engine:**
   - Implemented `compute_spearman_ic(factor_scores, forward_returns)` using `scipy.stats.spearmanr`.
   - Robust edge-case handling: detects sample sizes $< 15$, zero-variance constant series, and non-finite NaN/Inf values, cleanly returning $(0.0, 1.0)$ without throwing runtime warnings.

3. **Statistical Pruning Hurdle & Dynamic Information Ratio Weighting:**
   - Implemented `evaluate_indicators()`:
     - Computes out-of-sample mean IC, sample standard deviation of IC, and Information Ratio across cross-validation folds.
     - **Independent Pruning Gate (D-04):** Automatically sets `is_pruned = True` and `weight = 0.0` for any indicator with mean out-of-sample $IC \le 0$ or $p > 0.05$ (or non-positive IR).
     - **Dynamic IR Weighting:** Valid unpruned indicators receive weights proportional to $IR = \text{mean}(IC) / \text{std}(IC)$, normalized to sum to exactly $1.0000$.
     - **Fallback Mode:** If all indicators fail the hurdle, all weights default to $0.0$, and status is tagged as `UNPROVEN_NOISE`.

4. **Composite Score & Directional Classification:**
   - Implemented `compute_composite_score()`:
     - Returns `(composite_score, signal_label)`.
     - When all indicators are pruned, safely outputs `(50.0, "UNPROVEN_NOISE")`.
     - When valid indicators exist: scores $\ge 60.0 \implies \text{"BUY"}$, $\le 40.0 \implies \text{"SELL"}$, otherwise $\text{"HOLD"}$.

5. **Comprehensive Verification & Quality Gates:**
   - Created `tests/test_indicator_evaluator.py` covering:
     - Factor score bounds and flat/extreme monotonic price trends.
     - Known correlation benchmarks (+1.0, -1.0, constant, random noise).
     - Single-fold and multi-fold evaluations.
     - In-sample vs out-of-sample ICs and p-values.
     - Statistical pruning gates and IR weight normalization.
     - Fallback behavior and composite score directional outcomes.
   - Enhanced `tests/test_execution.py` to cover `_refresh_on_demand_price()`, achieving 96.72% coverage on `slippage_check.py`.
   - Full suite passing: 269 tests passed in 15.69s.
   - Safety-critical coverage: 100% on `src/execution/`, `src/config/`, and `src/api/middleware.py`.
