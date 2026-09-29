---
phase: 05-make-the-signal-engine-evidence-based
plan: 07
status: complete
commits: 1
completed_at: 2026-09-29T13:56:00Z
---

# Plan 05-07: Signal Scheduler Jobs, REST Endpoints & Systematic Terminology Migration Summary

## Overview

Plan 05-07 completed Phase 5 ("Make the Signal Engine Evidence-Based") by implementing end-of-day signal snapshot scheduling, asynchronous forward outcome maturation, versioned REST API endpoints under `/api/v1/signals/*`, backward-compatible aliasing of legacy endpoints with HTTP 299 Deprecation Warning headers, and systematic migration of user-facing terminology from heuristic "predictions" to evidence-based "quantitative signals" (D-01, D-02, D-08).

## Key Accomplishments

1. **Signal Snapshot Capture & Maturation Engine (`src/analytics/signal_recorder.py`):**
   - `compute_holding_signal`: Synthesizes factor score evaluations, out-of-sample Spearman rank IC weights, calibrated Student's t Monte Carlo cones (P10..P90), and walk-forward evidence hurdle status into a typed `HoldingSignalDTO`.
   - `compute_portfolio_signals`: Bulk evaluates quantitative signals for all active portfolio holdings.
   - `record_eod_signals`: Captures and persists daily holding signals and benchmark prices in `signal_snapshots` with duplicate idempotency.
   - `mature_forward_returns`: Queries pending snapshots where 5, 20, or 60 trading days have elapsed, computes realized forward returns for stock and benchmark, calculates excess alpha, and updates DB records.
   - `run_and_record_backtest`: Triggers rolling walk-forward backtest and persists run metadata in `backtest_runs`.

2. **APScheduler Background Jobs (`src/scheduler/jobs.py`):**
   - Registered `record_daily_signals_job` running daily at 16:15 IST (post-market close).
   - Registered `mature_forward_returns_job` running daily at 16:30 IST.
   - Enforced market day gating via `is_market_day()` to avoid executing on trading holidays or weekends.

3. **REST API Endpoints & Systematic Terminology Migration (`src/api/v1/blueprint.py`):**
   - `GET /api/v1/signals/<symbol>`: Returns complete `HoldingSignalDTO` with calibrated Monte Carlo percentiles (P10, P50, P90) and no point target price.
   - `GET /api/v1/signals`: Returns quantitative signals for all portfolio holdings.
   - `POST /api/v1/signals/backtest`: Triggers and returns out-of-sample walk-forward backtest (`BacktestRunDTO`).
   - `GET /api/v1/signals/history`: Retrieves historical daily snapshots with realized multi-horizon forward returns.
   - `GET /api/v1/analysis/<symbol>`: Aliased to modern signal engine with HTTP header `Warning: 299 - "Deprecated endpoint. Use /api/v1/signals/<symbol> instead."`.

4. **Comprehensive Test Suite & Quality Gates:**
   - Authored `tests/test_signal_recorder.py` with 10 unit tests covering signal calculation, EOD recording, maturation updates across 5d/20d/60d, and scheduler job triggers.
   - Authored `tests/test_signals_api.py` with 7 integration tests covering API authentication, payload validation, Monte Carlo percentile contracts, backtest triggering, snapshot history, and legacy 299 warning header.
   - 329 total tests passing in 15.20s.
   - Overall project coverage reached 90.94% (exceeding 75% gate).
   - 100% safety-critical coverage maintained across `src/execution/`, `src/config/`, and `src/api/middleware.py`.
   - Zero Ruff lint or format issues across 153 files.
