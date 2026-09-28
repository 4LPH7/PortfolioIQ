---
phase: 05-make-the-signal-engine-evidence-based
plan: 01
status: complete
commits: 1
completed_at: 2026-09-28T21:30:00+05:30
---

# Plan 05-01: Database Migrations & Data Layer Summary

## Overview

Plan 05-01 established the foundational PostgreSQL schemas, typed Pydantic DTO models, and session-managed database repository functions required for evidence-based quantitative signal tracking, walk-forward backtest audits, and historical bar caching in Phase 5.

## Key Accomplishments

1. **Database Migrations:**
   - `019_signal_snapshots.sql`: Created `signal_snapshots` table supporting daily EOD holding signals, JSONB indicators, calibrated Monte Carlo dispersion percentiles, model versioning, and multi-horizon forward returns (5d, 20d, 60d) for individual assets, NIFTY 50 TRI benchmark, and excess alpha.
   - `020_backtest_runs.sql`: Created `backtest_runs` and `indicator_evaluations` tables recording walk-forward cross-validation runs, strategy net performance, dual-baseline attributions (Stock B&H and NIFTY 50 TRI B&H), hurdle evaluations (`PROVEN_EDGE` vs `UNPROVEN_NOISE`), and independent indicator Spearman IC and Information Ratio metrics.
   - `021_historical_daily_bars.sql`: Created `historical_daily_bars` table to cache daily OHLCV bars locally for equities and benchmarks, eliminating external network latency and rate limits.
2. **Pydantic DTO Models (`src/models/dtos.py`):**
   - Added `IndicatorEvaluationDTO` (mean IC, std IC, IR, p-value, dynamic weight, pruning status).
   - Added `CalibratedMonteCarloDTO` (Student's t degrees of freedom, empirical coverage, percentiles P10..P90).
   - Added `BacktestRunDTO` (walk-forward partitions, strategy metrics, dual baselines, cost drag, child evaluations).
   - Added `SignalSnapshotDTO` and `CreateSignalSnapshotDTO`.
   - Added `HoldingSignalDTO` (replacing `HoldingPrediction`).
   - Added `HistoricalBarDTO`.
3. **Repository Methods (`src/db/repository.py`):**
   - `record_signal_snapshot()`: Upserts daily holding signal snapshots by `(user_id, tradingsymbol, snapshot_date, model_version)`.
   - `get_signal_snapshots()`: Retrieves chronological signal snapshot series.
   - `get_pending_forward_return_snapshots()`: Queries snapshots awaiting forward return maturation (5d, 20d, 60d).
   - `update_signal_forward_returns()`: Updates realized forward returns and alpha upon maturation.
   - `record_backtest_run()`: Upserts walk-forward backtest runs and cascades child indicator evaluations.
   - `get_latest_backtest_run()`: Queries the most recent backtest run with all indicator evaluations.
   - `upsert_historical_bars()`: Batch upserts daily OHLCV bars.
   - `get_historical_bars()`: Retrieves chronological bar series for backtesting.
4. **Verification & Testing:**
   - Applied migrations 019, 020, and 021 cleanly to PostgreSQL.
   - Extended `tests/test_migrations.py` with `test_phase_5_table_structures()`.
   - Extended `tests/test_repository.py` with comprehensive unit tests for all new repository functions.
   - Formatted and linted cleanly with Ruff.
   - All 243 tests passing with 100% safety-critical coverage.
