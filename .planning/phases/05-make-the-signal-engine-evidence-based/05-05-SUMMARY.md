---
phase: 05-make-the-signal-engine-evidence-based
plan: 05
status: complete
commits: 1
completed_at: 2026-09-29T13:30:00Z
---

# Plan 05-05: Calibrated Fat-Tailed Monte Carlo Simulation & Percentile Dispersion Summary

## Overview

Plan 05-05 implemented PortfolioIQ's calibrated fat-tailed Monte Carlo simulation module (`src/analytics/calibrated_monte_carlo.py`) and associated test suite (`tests/test_monte_carlo.py`). It deprecates Gaussian geometric Brownian motion in favor of a 3-parameter Student's $t$ distribution ($r_t \sim t_{\nu}(\mu, s)$) fitted via Maximum Likelihood Estimation (`scipy.stats.t.fit`). It calibrates prediction cones using empirical conformal scaling to guarantee that 80% confidence bands match historical realized frequencies, and strictly bans point forecasts ("Target Price") in favor of probabilistic dispersion percentiles ($P_{10}, P_{25}, P_{50}, P_{75}, P_{90}$) and daily fan chart trajectories returning `CalibratedMonteCarloDTO` (D-07).

## Key Accomplishments

1. **Student's $t$ Maximum Likelihood Estimation (`fit_student_t_parameters`):**
   - Fitted degrees of freedom $\nu$, location $\mu$, and scale $s$ to daily log returns.
   - Enforced tail-risk realism and numerical stability by clamping degrees of freedom to $\nu \in [2.5, 30.0]$.
   - Applied minimum scale floor ($s \ge 1e-4$) to prevent division by zero on flat price series.
   - Implemented robust fallbacks for small sample sizes ($<15$ bars) or non-finite values.

2. **Empirical Coverage Calibration (`calibrate_empirical_coverage`):**
   - Extracted theoretical quantile $q_{\text{theo}} = t_{\nu}^{-1}(0.90)$ for central 80% coverage ($[P_{10}, P_{90}]$).
   - Calculated standardized absolute residuals $e_t = \frac{|r_t - \mu|}{s}$ and determined the empirical 80th percentile $q_{\text{emp}}$.
   - Scaled the dispersion cone by multiplier $c = \max(1.0, q_{\text{emp}} / q_{\text{theo}})$, expanding the confidence cone whenever empirical residuals exhibit heavy tails.
   - Guaranteed historical coverage matches or exceeds 80%.

3. **Multi-Path Simulation & Dispersion Percentiles (`run_calibrated_monte_carlo`):**
   - Simulated 1,000+ path trajectories across the forward horizon (e.g. 30 trading days) using `scipy.stats.t.rvs`.
   - Reconstructed asset prices via cumulative log-return exponentiation: $P_t = P_0 \cdot \exp(\sum_{\tau=1}^t r_{\tau})$.
   - Calculated terminal dispersion percentiles ($P_{10}, P_{25}, P_{50}, P_{75}, P_{90}$) and probability of profit ($P(P_T > P_0)$).
   - Generated daily fan chart series ($fan\_p10, fan\_p50, fan\_p90$) along future calendar dates.
   - Strictly banned single point forecast fields ("Target Price"), outputting `CalibratedMonteCarloDTO`.

4. **Comprehensive Test Suite & Quality Gates:**
   - Authored `tests/test_monte_carlo.py` with 15 tests covering MLE parameter recovery, tail bound clamping, conformal coverage calibration, strict monotonic percentile ordering ($P_{10} < P_{25} < P_{50} < P_{75} < P_{90}$), absence of point estimates, deterministic replay with seed, and edge cases (short series, flat prices, invalid inputs).
   - 294 total test suite passing in 22.43s.
   - 89.96% overall test coverage (above 75% requirement).
   - 100% safety-critical coverage maintained across execution, config, and middleware.
   - Zero Ruff lint or format issues across 146 files.
